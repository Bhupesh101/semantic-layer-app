from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from ..config import RuntimeSettings, settings
from ..db import get_db
from ..models import LLMCall, Provider
from ..services.runtime_settings import get_runtime, save_runtime

router = APIRouter(prefix="/api", tags=["settings"])


@router.get("/settings", response_model=RuntimeSettings)
def read_settings(db: Session = Depends(get_db)):
    return get_runtime(db)


@router.put("/settings", response_model=RuntimeSettings)
def write_settings(body: RuntimeSettings, db: Session = Depends(get_db)):
    for pid in (body.generation_provider_id, body.chat_provider_id):
        if pid and db.get(Provider, pid) is None:
            raise HTTPException(422, f"Unknown provider {pid}")
    return save_runtime(db, body)


@router.get("/settings/limits")
def static_limits():
    return {"max_upload_mb": settings.max_upload_mb}


@router.get("/usage")
def usage(limit: int = 100, purpose: Optional[str] = None, db: Session = Depends(get_db)):
    """P8-03: recent LLM calls plus totals per provider/model."""
    q = db.query(LLMCall)
    if purpose:
        q = q.filter(LLMCall.purpose == purpose)
    calls = q.order_by(LLMCall.created_at.desc()).limit(min(limit, 500)).all()
    totals = (
        db.query(
            LLMCall.provider_name,
            LLMCall.model,
            func.count(),
            func.sum(LLMCall.tokens_in),
            func.sum(LLMCall.tokens_out),
            func.sum(LLMCall.cost_usd),
            func.avg(LLMCall.latency_ms),
            func.sum(case((LLMCall.error.is_(None), 0), else_=1)),
        )
        .group_by(LLMCall.provider_name, LLMCall.model)
        .all()
    )
    return {
        "calls": [
            {
                "id": c.id,
                "purpose": c.purpose,
                "provider": c.provider_name,
                "model": c.model,
                "tokens_in": c.tokens_in,
                "tokens_out": c.tokens_out,
                "cost_usd": c.cost_usd,
                "latency_ms": c.latency_ms,
                "error": c.error,
                "has_payload": c.payload is not None,
                "created_at": c.created_at,
            }
            for c in calls
        ],
        "totals": [
            {
                "provider": t[0],
                "model": t[1],
                "calls": t[2],
                "tokens_in": t[3] or 0,
                "tokens_out": t[4] or 0,
                "cost_usd": round(t[5], 4) if t[5] is not None else None,
                "avg_latency_ms": round(t[6]) if t[6] is not None else None,
                "errors": t[7] or 0,
            }
            for t in totals
        ],
    }


@router.get("/usage/{cid}/payload")
def call_payload(cid: str, db: Session = Depends(get_db)):
    """P8-04: exact payload of a call (recorded only while debug mode was on)."""
    c = db.get(LLMCall, cid)
    if c is None:
        raise HTTPException(404, "Call not found")
    return {"payload": c.payload}
