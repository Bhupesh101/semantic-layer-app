"""LLM provider configuration, clients, model discovery and connection tests (P1)."""

import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
import yaml
from openai import AsyncOpenAI, OpenAI
from sqlalchemy.orm import Session

from ..crypto import decrypt
from ..models import Provider
from .llm_log import record_call
from .runtime_settings import get_runtime

DEFAULTS_FILE = Path(__file__).resolve().parent.parent / "provider_defaults.yaml"
PROVIDER_TYPES = ("openrouter", "ollama", "openai_compatible")


@lru_cache(maxsize=1)
def provider_defaults() -> Dict[str, Any]:
    return yaml.safe_load(DEFAULTS_FILE.read_text())


def effective_params(p: Provider) -> Dict[str, Any]:
    return {**provider_defaults()["params"], **(p.params or {})}


def _api_key(p: Provider) -> str:
    key = decrypt(p.api_key_enc)
    if key:
        return key
    # The OpenAI SDK requires some key; Ollama ignores it.
    return provider_defaults().get(p.type, {}).get("api_key_placeholder") or "not-needed"


def _headers(p: Provider) -> Dict[str, str]:
    h = p.extra_headers or {}
    out = {}
    if p.type == "openrouter":
        if h.get("http_referer"):
            out["HTTP-Referer"] = h["http_referer"]
        if h.get("x_title"):
            out["X-Title"] = h["x_title"]
    return out


def client(p: Provider) -> OpenAI:
    prm = effective_params(p)
    return OpenAI(
        base_url=p.base_url,
        api_key=_api_key(p),
        timeout=float(prm["timeout_s"]),
        max_retries=int(prm["retries"]),
        default_headers=_headers(p),
    )


def async_client(p: Provider) -> AsyncOpenAI:
    prm = effective_params(p)
    return AsyncOpenAI(
        base_url=p.base_url,
        api_key=_api_key(p),
        timeout=float(prm["timeout_s"]),
        max_retries=int(prm["retries"]),
        default_headers=_headers(p),
    )


def resolve_provider(
    db: Session, purpose: str, provider_id: Optional[str] = None, model: Optional[str] = None
) -> Tuple[Provider, str]:
    """Pick provider + model: explicit > per-purpose default (P1-07) > global default."""
    rt = get_runtime(db)
    pid = provider_id or getattr(rt, f"{purpose}_provider_id", None)
    p = db.get(Provider, pid) if pid else None
    if p is None:
        p = db.query(Provider).filter(Provider.is_default.is_(True)).first()
    if p is None:
        p = db.query(Provider).order_by(Provider.created_at).first()
    if p is None:
        raise LookupError("No LLM provider configured. Add one on the Providers page.")
    chosen = model
    if not chosen and not provider_id and pid == p.id:
        chosen = getattr(rt, f"{purpose}_model", None)
    chosen = chosen or p.default_model
    if not chosen:
        raise LookupError(f"Provider '{p.name}' has no default model set.")
    return p, chosen


def list_models(type_: str, base_url: str, api_key: Optional[str], headers: Dict[str, str]) -> List[Dict[str, Any]]:
    """Fetch /models (P1-03). For OpenRouter, keep context/pricing/tool metadata (P1-04)."""
    url = base_url.rstrip("/") + "/models"
    hdrs = dict(headers)
    if api_key:
        hdrs["Authorization"] = f"Bearer {api_key}"
    r = httpx.get(url, headers=hdrs, timeout=15)
    r.raise_for_status()
    data = r.json().get("data", [])
    out = []
    for m in data:
        item: Dict[str, Any] = {"id": m.get("id")}
        if type_ == "openrouter":
            pricing = m.get("pricing") or {}
            item.update(
                {
                    "name": m.get("name"),
                    "context_length": m.get("context_length"),
                    "prompt_price": _per_million(pricing.get("prompt")),
                    "completion_price": _per_million(pricing.get("completion")),
                    "supports_tools": "tools" in (m.get("supported_parameters") or []),
                }
            )
        out.append(item)
    return sorted([m for m in out if m["id"]], key=lambda m: m["id"])


def _per_million(v: Any) -> Optional[float]:
    try:
        return round(float(v) * 1_000_000, 4)
    except (TypeError, ValueError):
        return None


def estimate_cost(p: Provider, model: str, tokens_in: Optional[int], tokens_out: Optional[int]) -> Optional[float]:
    m = next((x for x in (p.available_models or []) if x.get("id") == model), None)
    if not m or m.get("prompt_price") is None or tokens_in is None:
        return None
    return round(
        (tokens_in * m["prompt_price"] + (tokens_out or 0) * (m.get("completion_price") or 0)) / 1_000_000,
        6,
    )


def test_connection(db: Session, p: Provider, model: Optional[str] = None) -> Dict[str, Any]:
    """P1-05: a minimal completion."""
    model = model or p.default_model
    started = time.perf_counter()
    try:
        resp = (
            client(p)
            .with_options(max_retries=0)
            .chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Reply with the single word: OK"}],
                max_tokens=10,
                temperature=0,
            )
        )
        ms = round((time.perf_counter() - started) * 1000)
        text = (resp.choices[0].message.content or "").strip()
        record_call(db, "test", p, model, resp.usage, ms)
        return {"ok": True, "latency_ms": ms, "reply": text[:200], "model": model}
    except Exception as e:  # noqa: BLE001 - surface any provider error to the user
        ms = round((time.perf_counter() - started) * 1000)
        record_call(db, "test", p, model, None, ms, error=str(e))
        return {"ok": False, "latency_ms": ms, "error": _short_error(e), "model": model}


_PING_TOOL = {
    "type": "function",
    "function": {
        "name": "get_order_total",
        "description": "Returns the total for an order id.",
        "parameters": {
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
    },
}


def test_tools(db: Session, p: Provider, model: Optional[str] = None) -> Dict[str, Any]:
    """P1-06: does the model emit a native tool call for a trivial tool?"""
    model = model or p.default_model
    started = time.perf_counter()
    try:
        resp = (
            client(p)
            .with_options(max_retries=0)
            .chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "What is the total for order A-17? Use the tool."}],
                tools=[_PING_TOOL],
                max_tokens=100,
                temperature=0,
            )
        )
        ms = round((time.perf_counter() - started) * 1000)
        calls = resp.choices[0].message.tool_calls or []
        supports = any(c.function.name == "get_order_total" for c in calls)
        record_call(db, "test", p, model, resp.usage, ms)
        if model == p.default_model:
            p.supports_tools = supports
            db.commit()
        return {"ok": True, "supports_tools": supports, "latency_ms": ms, "model": model}
    except Exception as e:  # noqa: BLE001
        ms = round((time.perf_counter() - started) * 1000)
        record_call(db, "test", p, model, None, ms, error=str(e))
        msg = _short_error(e)
        if "tool" in msg.lower():
            if model == p.default_model:
                p.supports_tools = False
                db.commit()
            return {"ok": True, "supports_tools": False, "latency_ms": ms, "model": model, "error": msg}
        return {"ok": False, "latency_ms": ms, "error": msg, "model": model}


def _short_error(e: Exception) -> str:
    msg = getattr(e, "message", None) or str(e)
    return msg if len(msg) < 500 else msg[:500] + "…"
