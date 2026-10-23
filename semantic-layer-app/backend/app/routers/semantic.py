import json
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import DataSource, Job, SemanticModelVersion
from ..semantic import generator, versions
from ..semantic.schema import SemanticModel
from ..semantic.validate import parse_model, test_metric, validate_model
from ..services.runtime_settings import get_runtime
from .sources import get_source, source_out, version_summary

router = APIRouter(prefix="/api", tags=["semantic"])


def _version(db: Session, vid: str) -> SemanticModelVersion:
    v = db.get(SemanticModelVersion, vid)
    if v is None:
        raise HTTPException(404, "Semantic model version not found")
    return v


def _require_profile(s: DataSource) -> None:
    if s.profile is None or not s.parquet_path:
        raise HTTPException(409, "Profile the data source first.")


class GenerateIn(BaseModel):
    provider_id: Optional[str] = None
    model: Optional[str] = None
    sample_values: Optional[int] = Field(None, ge=0, le=20)
    strict_privacy: Optional[bool] = None
    instructions: str = Field("", max_length=5000)


@router.post("/sources/{sid}/semantic/payload-preview")
def payload_preview(sid: str, body: GenerateIn = GenerateIn(), db: Session = Depends(get_db)):
    """Exactly what generation will send (P3-03)."""
    s = get_source(db, sid)
    _require_profile(s)
    rt = get_runtime(db)
    n = rt.sample_values_per_column if body.sample_values is None else body.sample_values
    strict = rt.strict_privacy if body.strict_privacy is None else body.strict_privacy
    payload = generator.build_profile_payload(s, s.profile.data, n, strict)
    messages = generator.build_messages(payload, body.instructions)
    return {
        "sample_values": n,
        "strict_privacy": strict,
        "messages": messages,
        "approx_tokens": len(json.dumps(messages)) // 4,
    }


@router.post("/sources/{sid}/semantic/generate", status_code=202)
def generate(sid: str, body: GenerateIn = GenerateIn(), db: Session = Depends(get_db)):
    s = get_source(db, sid)
    _require_profile(s)
    running = db.query(Job).filter(Job.source_id == sid, Job.status.in_(["pending", "running"])).first()
    if running:
        raise HTTPException(409, {"message": "A generation job is already running.", "job_id": running.id})
    try:
        job = generator.start_generation_job(
            db, s, body.provider_id, body.model, body.instructions, body.sample_values, body.strict_privacy
        )
    except LookupError as e:
        raise HTTPException(409, str(e)) from e
    return job_out(job)


def job_out(j: Job) -> Dict[str, Any]:
    return {
        "id": j.id,
        "kind": j.kind,
        "source_id": j.source_id,
        "status": j.status,
        "progress": j.progress,
        "error": j.error,
        "result_version_id": j.result_version_id,
        "created_at": j.created_at,
        "updated_at": j.updated_at,
    }


@router.get("/jobs/{jid}")
def get_job(jid: str, db: Session = Depends(get_db)):
    j = db.get(Job, jid)
    if j is None:
        raise HTTPException(404, "Job not found")
    return job_out(j)


@router.get("/sources/{sid}/jobs/latest")
def latest_job(sid: str, db: Session = Depends(get_db)):
    j = db.query(Job).filter(Job.source_id == sid).order_by(Job.created_at.desc()).first()
    return job_out(j) if j else None


@router.post("/jobs/{jid}/cancel")
def cancel_job(jid: str, db: Session = Depends(get_db)):
    j = db.get(Job, jid)
    if j is None:
        raise HTTPException(404, "Job not found")
    if j.status in ("pending", "running"):
        j.cancel_requested = True
        j.progress = [*j.progress, "Cancel requested; stopping after the current call"]
        db.commit()
    return job_out(j)


def version_out(db: Session, v: SemanticModelVersion, issues: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
    s = v.source
    if issues is None:
        model, issues = parse_model(v.model_json)
        if model is not None:
            issues = validate_model(model, s.profile.data, Path(s.parquet_path))
    newer = s.active_semantic_version_id
    return {
        **version_summary(v),
        "source_id": s.id,
        "model": v.model_json,
        "base": v.base_json,
        "issues": issues,
        "is_active": newer == v.id,
    }


@router.get("/semantic/{vid}")
def get_version(vid: str, db: Session = Depends(get_db)):
    return version_out(db, _version(db, vid))


class SaveIn(BaseModel):
    model: Dict[str, Any]
    change_notes: Optional[str] = Field(None, max_length=5000)


@router.put("/semantic/{vid}")
def save_version(vid: str, body: SaveIn, db: Session = Depends(get_db)):
    """Save draft edits (P4-03, P4-10, P4-11). Schema errors reject the save."""
    v = _version(db, vid)
    model, errs = parse_model(body.model)
    if model is None:
        raise HTTPException(422, {"message": "The model does not match the schema.", "issues": errs})
    try:
        versions.save_draft_model(db, v, model, body.change_notes)
    except versions.VersionError as e:
        raise HTTPException(409, str(e)) from e
    return version_out(db, v)


@router.post("/semantic/{vid}/validate")
def validate_version(vid: str, body: Optional[SaveIn] = None, db: Session = Depends(get_db)):
    v = _version(db, vid)
    model, errs = parse_model(body.model if body else v.model_json)
    if model is None:
        return {"issues": errs}
    return {"issues": validate_model(model, v.source.profile.data, Path(v.source.parquet_path))}


@router.post("/semantic/{vid}/accept-all")
def accept_all(vid: str, db: Session = Depends(get_db)):
    """P4-07: mark every remaining item accepted."""
    v = _version(db, vid)
    if v.status != "draft":
        raise HTTPException(409, "Only drafts can be edited.")
    versions.accept_all(db, v)
    return version_out(db, v)


class RegenerateIn(BaseModel):
    item_type: Literal["column", "metric"]
    name: str
    instructions: str = Field("", max_length=2000)
    provider_id: Optional[str] = None
    model: Optional[str] = None


@router.post("/semantic/{vid}/regenerate-item")
def regenerate_item(vid: str, body: RegenerateIn, db: Session = Depends(get_db)):
    """P3-12 / P4-06: regenerate one column or metric with optional instructions."""
    v = _version(db, vid)
    if v.status != "draft":
        raise HTTPException(409, "Only drafts can be edited.")
    try:
        item = generator.regenerate_item(
            db, v, body.item_type, body.name, body.instructions, body.provider_id, body.model
        )
    except (ValueError, LookupError) as e:
        raise HTTPException(422, str(e)) from e
    except Exception as e:  # noqa: BLE001 - provider errors
        raise HTTPException(502, f"The LLM call failed: {e}") from e
    versions.replace_item(db, v, body.item_type, item)
    return version_out(db, v)


@router.post("/semantic/{vid}/approve")
def approve(vid: str, db: Session = Depends(get_db)):
    v = _version(db, vid)
    try:
        issues = versions.approve(db, v)
    except versions.VersionError as e:
        raise HTTPException(409, str(e)) from e
    if v.status != "approved":
        raise HTTPException(422, {"message": "Fix the errors before approving.", "issues": issues})
    return {"version": version_out(db, v, issues), "source": source_out(v.source)}


@router.post("/semantic/{vid}/rollback")
def rollback(vid: str, db: Session = Depends(get_db)):
    v = _version(db, vid)
    try:
        versions.rollback(db, v)
    except versions.VersionError as e:
        raise HTTPException(409, str(e)) from e
    return {"version": version_out(db, v), "source": source_out(v.source)}


@router.delete("/semantic/{vid}")
def discard_draft(vid: str, db: Session = Depends(get_db)):
    v = _version(db, vid)
    if v.status != "draft":
        raise HTTPException(409, "Only drafts can be discarded.")
    s = v.source
    db.delete(v)
    db.flush()
    if s.status == "in_review" and not s.active_semantic_version_id:
        s.status = "profiled"
    db.commit()
    return {"ok": True}


@router.post("/sources/{sid}/semantic/draft")
def new_draft(sid: str, db: Session = Depends(get_db)):
    """P5-04: edit an onboarded model through a new draft version."""
    s = get_source(db, sid)
    if not s.active_semantic_version_id:
        raise HTTPException(409, "This source has no approved model yet.")
    v = versions.new_draft_from(db, _version(db, s.active_semantic_version_id))
    return version_out(db, v)


class MetricTestIn(BaseModel):
    expression: str = Field(min_length=1, max_length=2000)
    filters: List[str] = Field(default_factory=list, max_length=10)
    model: Optional[Dict[str, Any]] = None


@router.post("/semantic/{vid}/test-metric")
def test_metric_endpoint(vid: str, body: MetricTestIn, db: Session = Depends(get_db)):
    """P4-04: validate a metric live against the first 10,000 rows."""
    v = _version(db, vid)
    model, errs = parse_model(body.model or v.model_json)
    if model is None:
        return {"ok": False, "error": "; ".join(e["message"] for e in errs)}
    return test_metric(
        model, Path(v.source.parquet_path), body.expression, body.filters, get_runtime(db).query_timeout_s
    )


@router.get("/semantic/{vid}/export")
def export(vid: str, format: Literal["yaml", "json"] = "yaml", db: Session = Depends(get_db)):
    """P5-08: portable, git-friendly export without review metadata."""
    v = _version(db, vid)
    data = SemanticModel.model_validate(v.model_json).model_dump(
        mode="json",
        exclude={
            "columns": {"__all__": {"provenance", "review_status"}},
            "metrics": {"__all__": {"provenance", "review_status"}},
            "entity": {"provenance"},
        },
    )
    fname = f"{v.source.name}-v{v.version}.{format}".replace(" ", "_")
    headers = {"Content-Disposition": f'attachment; filename="{fname}"'}
    if format == "json":
        return PlainTextResponse(json.dumps(data, indent=2), media_type="application/json", headers=headers)
    return PlainTextResponse(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True), media_type="application/yaml", headers=headers
    )


@router.post("/sources/{sid}/semantic/import")
async def import_model(sid: str, request: Request, db: Session = Depends(get_db)):
    """Body: the YAML or JSON text of an exported model."""
    s = get_source(db, sid)
    _require_profile(s)
    raw = await request.body()
    if len(raw) > 2_000_000:
        raise HTTPException(413, "File too large.")
    try:
        data = yaml.safe_load(raw.decode("utf-8", errors="replace"))
    except yaml.YAMLError as e:
        raise HTTPException(422, f"Not valid YAML/JSON: {e}") from e
    model, errs = parse_model(data)
    if model is None:
        raise HTTPException(422, {"message": "The file does not match the semantic schema.", "issues": errs})
    try:
        v = versions.create_user_draft(db, s, model, "Imported")
    except versions.VersionError as e:
        raise HTTPException(409, str(e)) from e
    return version_out(db, v)
