"""AI semantic-layer generation from the profile only (P3)."""

import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import openai
from sqlalchemy.orm import Session

from ..config import RuntimeSettings
from ..db import SessionLocal
from ..logging_setup import log
from ..models import DataSource, Job, Provider, SemanticModelVersion
from ..services.llm_log import record_call
from ..services.profiler import mask_value
from ..services.providers import client, effective_params, resolve_provider
from ..services.runtime_settings import get_runtime
from .schema import Column, SemanticModel, llm_output_schema
from .validate import has_errors, parse_model, validate_model

PROMPT_VERSION = "semantic_generation_v1"
PROMPT_FILE = Path(__file__).resolve().parent.parent / "prompts" / f"{PROMPT_VERSION}.md"
MAX_REPAIRS = 2
logger = logging.getLogger(__name__)


class Cancelled(Exception):
    pass


# --------------------------------------------------------------------------- payload


def build_profile_payload(source: DataSource, profile: Dict[str, Any], sample_n: int, strict: bool) -> Dict[str, Any]:
    """Exactly what the LLM may see about the data (P3-01, P3-02, P2-13)."""
    cols = []
    for c in profile["columns"]:
        pii = bool(c.get("pii"))
        item: Dict[str, Any] = {
            "name": c["name"],
            "original_name": c.get("original_name", c["name"]),
            "inferred_type": c["inferred_type"],
            "null_pct": c.get("null_pct"),
            "distinct_count": c.get("distinct_count"),
            "pii": pii,
        }
        for k in ("mean", "median", "std", "length"):
            if c.get(k) is not None:
                item[k] = c[k]
        if c.get("pattern_hints"):
            item["pattern_hints"] = c["pattern_hints"]
        if c.get("candidate_key"):
            item["candidate_key"] = True
        if c["inferred_type"] in ("integer", "float", "date", "datetime") and not pii:
            item["min"], item["max"] = c.get("min"), c.get("max")
        if not strict and sample_n > 0:
            tops = c.get("top_values") or []
            samples = c.get("sample_values") or []
            if pii:
                item["sample_values"] = [mask_value(v) for v in samples[:sample_n]]
            else:
                if tops:
                    item["top_values"] = tops[: max(sample_n, 10)]
                item["sample_values"] = samples[:sample_n]
        cols.append(item)
    return {
        "table": {
            "source_name": source.name,
            "original_filename": source.original_filename,
            "row_count": profile["row_count"],
            "candidate_keys": profile.get("candidate_keys", []),
        },
        "user_notes": source.description or "",
        "columns": cols,
    }


def build_messages(payload: Dict[str, Any], instructions: str = "") -> List[Dict[str, str]]:
    system = PROMPT_FILE.read_text().replace("{schema}", json.dumps(llm_output_schema(), indent=2))
    user = "PROFILE:\n" + json.dumps(payload, indent=1, default=str)
    if instructions.strip():
        user += "\n\nADDITIONAL INSTRUCTIONS FROM THE USER:\n" + instructions.strip()
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# --------------------------------------------------------------------------- LLM I/O


def extract_json(text: str) -> Any:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def _complete_json(db: Session, provider: Provider, model: str, messages: List[Dict[str, str]], purpose: str) -> str:
    prm = effective_params(provider)
    kwargs: Dict[str, Any] = dict(
        model=model,
        messages=messages,
        temperature=float(prm["temperature"]),
        max_tokens=int(prm["max_tokens"]),
    )
    started = time.perf_counter()
    try:
        try:
            resp = client(provider).chat.completions.create(response_format={"type": "json_object"}, **kwargs)
        except openai.BadRequestError:
            # P3-05: not every provider/model supports JSON mode; fall back to parse + validate
            resp = client(provider).chat.completions.create(**kwargs)
    except Exception as e:
        record_call(db, purpose, provider, model, None, _ms(started), error=str(e), payload=messages)
        raise
    record_call(db, purpose, provider, model, resp.usage, _ms(started), payload=messages)
    return resp.choices[0].message.content or ""


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


# --------------------------------------------------------------------------- post-processing


def normalize(model: SemanticModel, profile: Dict[str, Any]) -> SemanticModel:
    """Keep exactly the profiled columns; profile PII flags always win (P2-13)."""
    prof_cols = {c["name"]: c for c in profile["columns"]}
    by_name = {c.name: c for c in model.columns if c.name in prof_cols}
    cols: List[Column] = []
    for name, pc in prof_cols.items():
        col = by_name.get(name) or Column(
            name=name,
            display_name=pc.get("original_name", name),
            data_type=pc["inferred_type"],
            role="dimension",
            confidence="low",
            rationale="The model did not describe this column; please review.",
        )
        if pc.get("pii"):
            col.pii = True
        if not col.display_name:
            col.display_name = pc.get("original_name", name)
        col.provenance, col.review_status = {}, "pending"
        cols.append(col)
    model.columns = cols
    for m in model.metrics:
        m.provenance, m.review_status = {}, "pending"
    model.entity.provenance = {}
    return model


def _errors_for_repair(issues: List[Dict[str, str]]) -> str:
    return "\n".join(f"- {i['path']}: {i['message']}" for i in issues if i["severity"] == "error")


def generate_model(
    db: Session,
    source: DataSource,
    provider: Provider,
    model_id: str,
    rt: RuntimeSettings,
    instructions: str = "",
    sample_n: Optional[int] = None,
    strict: Optional[bool] = None,
    progress=lambda msg: None,
    check_cancel=lambda: None,
) -> Tuple[SemanticModel, List[Dict[str, str]]]:
    profile = source.profile.data
    payload = build_profile_payload(
        source,
        profile,
        rt.sample_values_per_column if sample_n is None else sample_n,
        rt.strict_privacy if strict is None else strict,
    )
    messages = build_messages(payload, instructions)
    parquet = Path(source.parquet_path) if source.parquet_path else None
    last_model: Optional[SemanticModel] = None
    issues: List[Dict[str, str]] = []
    for attempt in range(MAX_REPAIRS + 1):
        check_cancel()
        progress(f"Calling {provider.name} / {model_id}" + (f" (repair {attempt})" if attempt else ""))
        text = _complete_json(db, provider, model_id, messages, "generation")
        check_cancel()
        try:
            data = extract_json(text)
            parsed, issues = parse_model(data)
        except (json.JSONDecodeError, ValueError) as e:
            parsed, issues = None, [{"path": "$", "message": f"Invalid JSON: {e}", "severity": "error"}]
        if parsed is not None:
            last_model = normalize(parsed, profile)
            issues = validate_model(last_model, profile, parquet)
        if parsed is not None and not has_errors(issues):
            progress("Draft is valid")
            return last_model, issues
        progress(f"Validation found {sum(i['severity'] == 'error' for i in issues)} error(s)")
        messages = messages + [
            {"role": "assistant", "content": text[:20000]},
            {
                "role": "user",
                "content": "Your JSON had these problems. Return the full corrected JSON "
                "object only.\n" + _errors_for_repair(issues),
            },
        ]
    if last_model is None:
        raise ValueError("The model did not return a usable semantic model: " + _errors_for_repair(issues)[:1000])
    progress("Saved with validation errors for you to fix in review")
    return last_model, issues


def save_draft(
    db: Session,
    source: DataSource,
    model: SemanticModel,
    provider: Provider,
    model_id: str,
    origin: str = "ai",
    notes: str = "",
) -> SemanticModelVersion:
    data = model.model_dump(mode="json")
    existing = next((v for v in source.versions if v.status == "draft"), None)
    if existing is not None:
        # Regenerating replaces the open draft instead of stacking drafts
        existing.model_json, existing.base_json, existing.origin = data, data, origin
        existing.generated_by_provider = provider.name if provider else None
        existing.generated_by_model = model_id
        existing.prompt_version = PROMPT_VERSION if origin == "ai" else None
        existing.change_notes = notes
        db.commit()
        return existing
    latest = max((v.version for v in source.versions), default=0)
    v = SemanticModelVersion(
        source_id=source.id,
        version=latest + 1,
        status="draft",
        origin=origin,
        model_json=data,
        base_json=data,
        generated_by_provider=provider.name if provider else None,
        generated_by_model=model_id,
        prompt_version=PROMPT_VERSION if origin == "ai" else None,
        change_notes=notes,
    )
    db.add(v)
    if source.status not in ("onboarded", "archived"):
        source.status = "in_review"
    db.commit()
    return v


# --------------------------------------------------------------------------- jobs (P3-10)


def start_generation_job(
    db: Session,
    source: DataSource,
    provider_id: Optional[str],
    model: Optional[str],
    instructions: str,
    sample_n: Optional[int],
    strict: Optional[bool],
) -> Job:
    provider, model_id = resolve_provider(db, "generation", provider_id, model)
    job = Job(
        kind="semantic_generation",
        source_id=source.id,
        status="pending",
        progress=[f"Queued with {provider.name} / {model_id}"],
    )
    db.add(job)
    db.commit()
    args = (job.id, source.id, provider.id, model_id, instructions, sample_n, strict)
    threading.Thread(target=_run_job, args=args, daemon=True).start()
    return job


def _run_job(job_id, source_id, provider_id, model_id, instructions, sample_n, strict) -> None:
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        job.status = "running"
        db.commit()

        def progress(msg: str) -> None:
            job.progress = [*job.progress, msg]
            db.commit()

        def check_cancel() -> None:
            db.refresh(job)
            if job.cancel_requested:
                raise Cancelled()

        source = db.get(DataSource, source_id)
        provider = db.get(Provider, provider_id)
        model, issues = generate_model(
            db,
            source,
            provider,
            model_id,
            get_runtime(db),
            instructions,
            sample_n,
            strict,
            progress,
            check_cancel,
        )
        check_cancel()
        v = save_draft(db, source, model, provider, model_id)
        job.result_version_id = v.id
        job.status = "succeeded"
        db.commit()
    except Cancelled:
        job.status = "cancelled"
        job.progress = [*job.progress, "Cancelled"]
        db.commit()
    except Exception as e:  # noqa: BLE001 - job errors are shown to the user
        log(logger, logging.ERROR, "generation failed", job_id=job_id, error=str(e))
        db.rollback()
        job = db.get(Job, job_id)
        job.status = "failed"
        job.error = str(e)[:2000]
        db.commit()
    finally:
        db.close()


# --------------------------------------------------------------------------- single item (P3-12)


def regenerate_item(
    db: Session,
    version: SemanticModelVersion,
    item_type: str,
    name: str,
    instructions: str,
    provider_id: Optional[str] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    source = version.source
    provider, model_id = resolve_provider(db, "generation", provider_id, model)
    rt = get_runtime(db)
    payload = build_profile_payload(source, source.profile.data, rt.sample_values_per_column, rt.strict_privacy)
    current = SemanticModel.model_validate(version.model_json)
    if item_type == "column":
        payload["columns"] = [c for c in payload["columns"] if c["name"] == name]
        if not payload["columns"]:
            raise ValueError(f"Unknown column '{name}'.")
        shape = llm_output_schema()["columns"][0]
    else:
        if current.metric(name) is None:
            raise ValueError(f"Unknown metric '{name}'.")
        shape = llm_output_schema()["metrics"][0]
    context = {
        "entity": current.entity.model_dump(include={"name", "description", "grain"}),
        "columns": [c.model_dump(include={"name", "role", "data_type", "description"}) for c in current.columns],
        "current_item": (current.column(name) if item_type == "column" else current.metric(name)).model_dump(
            exclude={"provenance", "review_status"}
        ),
    }
    messages = build_messages(payload, instructions)
    messages[0][
        "content"
    ] += f"\n\nNow regenerate ONE {item_type} only. Return a single JSON object with this shape:\n" + json.dumps(
        shape, indent=2
    )
    messages[1]["content"] += "\n\nCURRENT MODEL CONTEXT:\n" + json.dumps(context, indent=1)
    data = extract_json(_complete_json(db, provider, model_id, messages, "generation"))
    if not isinstance(data, dict):
        raise ValueError("The model did not return a JSON object.")
    if item_type == "column":
        data["name"] = name
        item = Column.model_validate(data)
        if any(c["name"] == name and c.get("pii") for c in source.profile.data["columns"]):
            item.pii = True
    else:
        from .schema import Metric

        data.setdefault("name", name)
        item = Metric.model_validate(data)
    return item.model_dump(mode="json")
