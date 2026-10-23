import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import DataSource, Profile, SemanticModelVersion
from ..semantic.schema import Column, SemanticModel
from ..semantic.versions import new_draft_from
from ..services import ingestion
from ..services.ingestion import ParseOptions
from ..services.profiler import profile_parquet, semantic_type
from ..services.runtime_settings import get_runtime

router = APIRouter(prefix="/api/sources", tags=["sources"])
CHUNK = 1024 * 1024


class SourceOut(BaseModel):
    id: str
    name: str
    description: str
    type: str
    original_filename: str
    row_count: Optional[int]
    column_count: Optional[int]
    size_bytes: int
    status: str
    error: Optional[str]
    parse_options: Dict[str, Any]
    ingest_report: Dict[str, Any]
    active_semantic_version_id: Optional[str]
    active_version: Optional[int]
    draft_version_id: Optional[str]
    created_at: datetime
    updated_at: datetime


def source_out(s: DataSource) -> SourceOut:
    active = next((v for v in s.versions if v.id == s.active_semantic_version_id), None)
    draft = next((v for v in s.versions if v.status == "draft"), None)
    return SourceOut(
        id=s.id,
        name=s.name,
        description=s.description,
        type=s.type,
        original_filename=s.original_filename,
        row_count=s.row_count,
        column_count=s.column_count,
        size_bytes=s.size_bytes,
        status=s.status,
        error=s.error,
        parse_options=s.parse_options or {},
        ingest_report=s.ingest_report or {},
        active_semantic_version_id=s.active_semantic_version_id,
        active_version=active.version if active else None,
        draft_version_id=draft.id if draft else None,
        created_at=s.created_at,
        updated_at=s.updated_at,
    )


def get_source(db: Session, sid: str) -> DataSource:
    s = db.get(DataSource, sid)
    if s is None:
        raise HTTPException(404, "Data source not found")
    return s


def _source_dir(sid: str) -> Path:
    return settings.uploads_dir / sid


async def _save_upload(request: Request, file: UploadFile, dest: Path) -> int:
    """Stream to disk, enforcing the size limit (P2-02)."""
    max_bytes = settings.max_upload_mb * 1024 * 1024
    declared = int(request.headers.get("content-length") or 0)
    if declared > max_bytes + 1024 * 1024:
        raise HTTPException(413, f"File is larger than the {settings.max_upload_mb} MB limit.")
    if not (file.filename or "").lower().endswith((".csv", ".tsv", ".txt")):
        raise HTTPException(415, "Only CSV files (.csv, .tsv, .txt) are supported for now.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    with open(dest, "wb") as f:
        while True:
            chunk = await file.read(CHUNK)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                f.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, f"File is larger than the {settings.max_upload_mb} MB limit.")
            f.write(chunk)
    if size == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(422, "The file is empty.")
    return size


@router.get("", response_model=List[SourceOut])
def list_sources(status: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(DataSource).order_by(DataSource.updated_at.desc())
    if status:
        q = q.filter(DataSource.status.in_(status.split(",")))
    return [source_out(s) for s in q]


@router.post("/upload", status_code=201)
async def upload(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
    s = DataSource(
        name=Path(file.filename or "data.csv").stem[:200],
        original_filename=file.filename or "data.csv",
        storage_path="",
        status="uploaded",
    )
    db.add(s)
    db.flush()
    dest = _source_dir(s.id) / "original.csv"
    try:
        s.size_bytes = await _save_upload(request, file, dest)
    except HTTPException:
        db.rollback()
        shutil.rmtree(_source_dir(s.id), ignore_errors=True)
        raise
    s.storage_path = str(dest)
    try:
        opts = ingestion.detect_options(dest)
        prev = ingestion.preview(dest, opts)
    except ValueError as e:
        opts, prev = ParseOptions(), None
        s.error = str(e)
    s.parse_options = opts.model_dump()
    db.commit()
    return {"source": source_out(s), "preview": prev, "error": s.error}


@router.post("/{sid}/preview")
def preview(sid: str, opts: ParseOptions, db: Session = Depends(get_db)):
    """Re-parse with user-overridden options (P2-03)."""
    s = get_source(db, sid)
    try:
        prev = ingestion.preview(Path(s.storage_path), opts)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    s.parse_options = opts.model_dump()
    s.error = None
    db.commit()
    return prev


class IngestIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=5000)
    options: ParseOptions


def _ingest(db: Session, s: DataSource, opts: ParseOptions) -> None:
    parquet = _source_dir(s.id) / "data.parquet"
    column_map, rows, report = ingestion.convert_to_parquet(Path(s.storage_path), parquet, opts)
    prof = profile_parquet(parquet, column_map, get_runtime(db).profile_sample_rows)
    s.parquet_path, s.column_map, s.row_count = str(parquet), column_map, rows
    s.column_count, s.ingest_report, s.parse_options = len(column_map), report, opts.model_dump()
    if s.profile is None:
        s.profile = Profile(source_id=s.id, data=prof)
    else:
        s.profile.data = prof
        s.profile.generated_at = datetime.utcnow()


@router.post("/{sid}/ingest", response_model=SourceOut)
def ingest(sid: str, body: IngestIn, db: Session = Depends(get_db)):
    """Convert to Parquet and profile (P2-07, P2-09). Status uploaded -> profiled."""
    s = get_source(db, sid)
    if s.status not in ("uploaded", "failed", "profiled"):
        raise HTTPException(409, f"Source is already {s.status}; use refresh to load new data.")
    s.name, s.description = body.name, body.description
    try:
        _ingest(db, s, body.options)
    except ValueError as e:
        s.status, s.error = "failed", str(e)
        db.commit()
        raise HTTPException(422, str(e)) from e
    s.status, s.error = "profiled", None
    db.commit()
    return source_out(s)


@router.get("/{sid}")
def detail(sid: str, db: Session = Depends(get_db)):
    s = get_source(db, sid)
    return {
        "source": source_out(s),
        "profile": s.profile.data if s.profile else None,
        "column_map": s.column_map,
        "versions": [version_summary(v) for v in reversed(s.versions)],
    }


def version_summary(v: SemanticModelVersion) -> Dict[str, Any]:
    return {
        "id": v.id,
        "version": v.version,
        "status": v.status,
        "origin": v.origin,
        "generated_by_provider": v.generated_by_provider,
        "generated_by_model": v.generated_by_model,
        "prompt_version": v.prompt_version,
        "approved_at": v.approved_at,
        "change_notes": v.change_notes,
        "created_at": v.created_at,
        "updated_at": v.updated_at,
    }


class SourcePatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=5000)


@router.patch("/{sid}", response_model=SourceOut)
def patch(sid: str, body: SourcePatch, db: Session = Depends(get_db)):
    s = get_source(db, sid)
    if body.name is not None:
        s.name = body.name
    if body.description is not None:
        s.description = body.description
    db.commit()
    return source_out(s)


def _status_without_archive(s: DataSource) -> str:
    if s.active_semantic_version_id:
        return "onboarded"
    if any(v.status == "draft" for v in s.versions):
        return "in_review"
    return "profiled" if s.profile else "uploaded"


@router.post("/{sid}/archive", response_model=SourceOut)
def archive(sid: str, db: Session = Depends(get_db)):
    s = get_source(db, sid)
    s.status = "archived"
    db.commit()
    return source_out(s)


@router.post("/{sid}/unarchive", response_model=SourceOut)
def unarchive(sid: str, db: Session = Depends(get_db)):
    s = get_source(db, sid)
    s.status = _status_without_archive(s)
    db.commit()
    return source_out(s)


@router.delete("/{sid}")
def delete(sid: str, db: Session = Depends(get_db)):
    """Delete metadata, the original file and the Parquet copy (P5-07)."""
    s = get_source(db, sid)
    db.delete(s)
    db.commit()
    shutil.rmtree(_source_dir(sid), ignore_errors=True)
    return {"ok": True}


@router.post("/{sid}/refresh")
async def refresh(sid: str, request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Re-upload data (P5-06): keep the model if the schema is compatible, else open a draft."""
    s = get_source(db, sid)
    if not s.parquet_path or s.profile is None:
        raise HTTPException(409, "Finish the first upload before refreshing.")
    staging = _source_dir(sid) / "refresh.csv"
    await _save_upload(request, file, staging)
    opts = ParseOptions(**(s.parse_options or {}))
    try:
        prev = ingestion.preview(staging, opts)
    except ValueError as e:
        staging.unlink(missing_ok=True)
        raise HTTPException(422, str(e)) from e
    old = {c["name"]: c["inferred_type"] for c in s.profile.data["columns"]}
    new = {c["name"]: semantic_type(c["type"]) for c in prev["columns"]}
    diff = {
        "added": sorted(set(new) - set(old)),
        "removed": sorted(set(old) - set(new)),
        "changed": sorted(c for c in set(old) & set(new) if old[c] != new[c]),
    }
    compatible = not any(diff.values())
    original = Path(s.storage_path)
    backup = original.with_suffix(".prev.csv")
    shutil.copyfile(original, backup)
    staging.replace(original)
    try:
        _ingest(db, s, opts)
    except ValueError as e:
        backup.replace(original)
        db.rollback()
        raise HTTPException(422, str(e)) from e
    backup.unlink(missing_ok=True)
    s.size_bytes = original.stat().st_size
    s.original_filename = file.filename or s.original_filename
    draft_id = None
    if not compatible and s.active_semantic_version_id:
        active = db.get(SemanticModelVersion, s.active_semantic_version_id)
        draft = new_draft_from(db, active, notes=f"Schema changed on refresh: {diff}")
        model = SemanticModel.model_validate(draft.model_json)
        prof_cols = {c["name"]: c for c in s.profile.data["columns"]}
        model.columns = [c for c in model.columns if c.name in prof_cols]
        for name in diff["added"]:
            pc = prof_cols[name]
            model.columns.append(
                Column(
                    name=name,
                    display_name=pc.get("original_name", name),
                    data_type=pc["inferred_type"],
                    role="dimension",
                    pii=bool(pc.get("pii")),
                    confidence="low",
                    rationale="New column from a data refresh; please review.",
                )
            )
        for c in model.columns:
            if c.name in diff["changed"]:
                c.data_type = prof_cols[c.name]["inferred_type"]
                c.confidence, c.rationale = "low", "Column type changed on refresh; please review."
        draft.model_json = draft.base_json = model.model_dump(mode="json")
        # The old model no longer matches the data, so chat is paused until the draft is approved
        active.status = "superseded"
        s.active_semantic_version_id = None
        s.status = "in_review"
        draft_id = draft.id
    db.commit()
    return {"source": source_out(s), "schema_diff": diff, "compatible": compatible, "draft_version_id": draft_id}
