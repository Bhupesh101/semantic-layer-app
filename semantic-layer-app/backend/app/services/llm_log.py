"""LLM call logging (P8-03) with optional exact payloads in debug mode (P8-04)."""

from typing import Any, Optional

from sqlalchemy.orm import Session

from ..models import LLMCall, Provider
from .runtime_settings import get_runtime


def record_call(
    db: Session,
    purpose: str,
    provider: Provider,
    model: str,
    usage: Any,
    latency_ms: int,
    error: Optional[str] = None,
    payload: Any = None,
) -> LLMCall:
    from .providers import estimate_cost  # avoid import cycle

    tin = getattr(usage, "prompt_tokens", None) if usage is not None else None
    tout = getattr(usage, "completion_tokens", None) if usage is not None else None
    if isinstance(usage, dict):
        tin, tout = usage.get("prompt_tokens"), usage.get("completion_tokens")
    row = LLMCall(
        purpose=purpose,
        provider_id=provider.id,
        provider_name=provider.name,
        model=model,
        tokens_in=tin,
        tokens_out=tout,
        cost_usd=estimate_cost(provider, model, tin, tout),
        latency_ms=latency_ms,
        error=(error or None) and error[:2000],
        # Payloads are messages + tool schemas only; API keys live in headers and never get here.
        payload=payload if payload is not None and get_runtime(db).debug_payloads else None,
    )
    db.add(row)
    db.commit()
    return row
