"""Semantic model versions: save with provenance, approve, rollback, new drafts (P4, P5)."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from ..models import DataSource, SemanticModelVersion
from .schema import COLUMN_EDITABLE, ENTITY_EDITABLE, METRIC_EDITABLE, SemanticModel
from .validate import has_errors, validate_model


class VersionError(ValueError):
    pass


def _prov_for(
    item: Dict[str, Any], base: Optional[Dict[str, Any]], fields: List[str], old_prov: Dict[str, str]
) -> Dict[str, str]:
    if base is None:
        return {f: "user" for f in fields}
    prov: Dict[str, str] = {}
    for f in fields:
        if item.get(f) != base.get(f):
            prov[f] = "user_override"
        elif old_prov.get(f) == "ai_regenerated":
            prov[f] = "ai_regenerated"
    return prov


def apply_provenance(new: Dict[str, Any], base: Dict[str, Any], old: Dict[str, Any]) -> Dict[str, Any]:
    """Recompute per-field provenance by comparing with the version's baseline (P4-08)."""
    base_cols = {c["name"]: c for c in base.get("columns", [])}
    old_cols = {c["name"]: c for c in old.get("columns", [])}
    for c in new.get("columns", []):
        c["provenance"] = _prov_for(
            c,
            base_cols.get(c["name"]),
            COLUMN_EDITABLE,
            old_cols.get(c["name"], {}).get("provenance", {}),
        )
    base_m = {m["name"]: m for m in base.get("metrics", [])}
    old_m = {m["name"]: m for m in old.get("metrics", [])}
    for m in new.get("metrics", []):
        m["provenance"] = _prov_for(
            m, base_m.get(m["name"]), METRIC_EDITABLE, old_m.get(m["name"], {}).get("provenance", {})
        )
    e = new.get("entity", {})
    e["provenance"] = _prov_for(e, base.get("entity", {}), ENTITY_EDITABLE, old.get("entity", {}).get("provenance", {}))
    return new


def has_overrides(model: Dict[str, Any]) -> bool:
    items = [model.get("entity", {}), *model.get("columns", []), *model.get("metrics", [])]
    return any(v in ("user_override", "user") for i in items for v in (i.get("provenance") or {}).values())


def save_draft_model(
    db: Session, v: SemanticModelVersion, model: SemanticModel, change_notes: Optional[str] = None
) -> SemanticModelVersion:
    if v.status != "draft":
        raise VersionError("Only drafts can be edited. Create a new draft from this version first.")
    data = apply_provenance(model.model_dump(mode="json"), v.base_json, v.model_json)
    v.model_json = data
    if v.origin != "user":
        v.origin = "ai+user" if has_overrides(data) else v.origin
    if change_notes is not None:
        v.change_notes = change_notes
    db.commit()
    return v


def replace_item(db: Session, v: SemanticModelVersion, item_type: str, item: Dict[str, Any]) -> SemanticModelVersion:
    """Put a regenerated column/metric into both the model and the baseline (P3-12, P4-06)."""
    if v.status != "draft":
        raise VersionError("Only drafts can be edited.")
    key = "columns" if item_type == "column" else "metrics"
    fields = COLUMN_EDITABLE if item_type == "column" else METRIC_EDITABLE
    item = {**item, "provenance": {f: "ai_regenerated" for f in fields}, "review_status": "pending"}
    model, base = dict(v.model_json), dict(v.base_json)
    for target in (model, base):
        items = list(target.get(key, []))
        idx = next((i for i, x in enumerate(items) if x["name"] == item["name"]), None)
        if idx is None:
            items.append(item)
        else:
            items[idx] = item
        target[key] = items
    v.model_json, v.base_json = model, base
    db.commit()
    return v


def accept_all(db: Session, v: SemanticModelVersion) -> SemanticModelVersion:
    model = dict(v.model_json)
    for key in ("columns", "metrics"):
        model[key] = [{**x, "review_status": "accepted"} for x in model.get(key, [])]
    v.model_json = model
    db.commit()
    return v


def approve(db: Session, v: SemanticModelVersion) -> List[Dict[str, str]]:
    """Validate, then make this the active version (P4-12, P5-01, P5-04)."""
    source: DataSource = v.source
    if v.status != "draft":
        raise VersionError("Only drafts can be approved.")
    model = SemanticModel.model_validate(v.model_json)
    issues = validate_model(model, source.profile.data, Path(source.parquet_path))
    if has_errors(issues):
        return issues
    accept_all(db, v)
    for other in source.versions:
        if other.status == "approved" and other.id != v.id:
            other.status = "superseded"
    v.status = "approved"
    v.approved_at = datetime.now(timezone.utc)
    source.active_semantic_version_id = v.id
    source.status = "onboarded"
    db.commit()
    return issues


def rollback(db: Session, v: SemanticModelVersion) -> None:
    """Re-activate a previously approved version (P5-05)."""
    if v.status not in ("superseded", "approved") or v.approved_at is None:
        raise VersionError("Only previously approved versions can be restored.")
    source: DataSource = v.source
    for other in source.versions:
        if other.status == "approved" and other.id != v.id:
            other.status = "superseded"
    v.status = "approved"
    source.active_semantic_version_id = v.id
    source.status = "onboarded"
    db.commit()


def new_draft_from(db: Session, v: SemanticModelVersion, notes: str = "") -> SemanticModelVersion:
    """Editing an approved model creates a new draft version (P5-04). Reuses an open draft."""
    source: DataSource = v.source
    existing = next((x for x in source.versions if x.status == "draft"), None)
    if existing is not None:
        return existing
    data = {**v.model_json}
    for key in ("columns", "metrics"):
        data[key] = [{**x, "provenance": {}, "review_status": "accepted"} for x in data.get(key, [])]
    data["entity"] = {**data.get("entity", {}), "provenance": {}}
    draft = SemanticModelVersion(
        source_id=source.id,
        version=max(x.version for x in source.versions) + 1,
        status="draft",
        origin=v.origin,
        model_json=data,
        base_json=data,
        generated_by_provider=v.generated_by_provider,
        generated_by_model=v.generated_by_model,
        prompt_version=v.prompt_version,
        change_notes=notes or f"Edited from v{v.version}",
    )
    db.add(draft)
    db.commit()
    return draft


def create_user_draft(db: Session, source: DataSource, model: SemanticModel, notes: str) -> SemanticModelVersion:
    """Import (P5-08): a new draft whose baseline is the imported model."""
    existing = next((x for x in source.versions if x.status == "draft"), None)
    if existing is not None:
        raise VersionError(f"Draft v{existing.version} already exists. Discard or approve it first.")
    data = model.model_dump(mode="json")
    v = SemanticModelVersion(
        source_id=source.id,
        version=max((x.version for x in source.versions), default=0) + 1,
        status="draft",
        origin="user",
        model_json=data,
        base_json=data,
        change_notes=notes,
    )
    db.add(v)
    if source.status not in ("onboarded", "archived"):
        source.status = "in_review"
    db.commit()
    return v
