from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..crypto import decrypt, encrypt, mask
from ..db import get_db
from ..models import ChatSession, Provider
from ..services import providers as svc
from ..services.runtime_settings import get_runtime, save_runtime

router = APIRouter(prefix="/api/providers", tags=["providers"])
ProviderType = Literal["openrouter", "ollama", "openai_compatible"]


class Params(BaseModel):
    temperature: Optional[float] = Field(None, ge=0, le=2)
    max_tokens: Optional[int] = Field(None, ge=16, le=200_000)
    timeout_s: Optional[float] = Field(None, ge=1, le=900)
    retries: Optional[int] = Field(None, ge=0, le=10)


class Headers(BaseModel):
    http_referer: Optional[str] = Field(None, max_length=500)
    x_title: Optional[str] = Field(None, max_length=200)


class ProviderIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    type: ProviderType
    base_url: str = Field(min_length=1, max_length=500)
    api_key: Optional[str] = Field(None, max_length=1000)
    default_model: str = Field("", max_length=300)
    params: Params = Params()
    extra_headers: Headers = Headers()
    is_default: bool = False


class ProviderPatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    base_url: Optional[str] = Field(None, min_length=1, max_length=500)
    api_key: Optional[str] = Field(None, max_length=1000)
    clear_api_key: bool = False
    default_model: Optional[str] = Field(None, max_length=300)
    params: Optional[Params] = None
    extra_headers: Optional[Headers] = None


class ProviderOut(BaseModel):
    id: str
    name: str
    type: str
    base_url: str
    api_key_masked: Optional[str]
    has_api_key: bool
    default_model: str
    available_models: List[Any]
    supports_tools: Optional[bool]
    is_default: bool
    params: Dict[str, Any]
    effective_params: Dict[str, Any]
    extra_headers: Dict[str, Any]
    created_at: datetime


def out(p: Provider) -> ProviderOut:
    key = decrypt(p.api_key_enc)
    return ProviderOut(
        id=p.id,
        name=p.name,
        type=p.type,
        base_url=p.base_url,
        api_key_masked=mask(key),
        has_api_key=bool(key),
        default_model=p.default_model,
        available_models=p.available_models or [],
        supports_tools=p.supports_tools,
        is_default=p.is_default,
        params=p.params or {},
        effective_params=svc.effective_params(p),
        extra_headers=p.extra_headers or {},
        created_at=p.created_at,
    )


def _get(db: Session, pid: str) -> Provider:
    p = db.get(Provider, pid)
    if p is None:
        raise HTTPException(404, "Provider not found")
    return p


def _set_default(db: Session, p: Provider) -> None:
    for other in db.query(Provider).filter(Provider.is_default.is_(True)):
        other.is_default = False
    p.is_default = True


@router.get("/defaults")
def defaults() -> Dict[str, Any]:
    return svc.provider_defaults()


@router.get("", response_model=List[ProviderOut])
def list_providers(db: Session = Depends(get_db)):
    return [out(p) for p in db.query(Provider).order_by(Provider.created_at)]


@router.post("", response_model=ProviderOut, status_code=201)
def create_provider(body: ProviderIn, db: Session = Depends(get_db)):
    d = svc.provider_defaults().get(body.type, {})
    if d.get("api_key_required") and not body.api_key:
        raise HTTPException(422, f"{d.get('label', body.type)} requires an API key.")
    is_first = db.query(Provider).count() == 0
    p = Provider(
        name=body.name,
        type=body.type,
        base_url=body.base_url.rstrip("/"),
        api_key_enc=encrypt(body.api_key),
        default_model=body.default_model,
        params=body.params.model_dump(exclude_none=True),
        extra_headers=body.extra_headers.model_dump(exclude_none=True),
        available_models=[],
    )
    db.add(p)
    if body.is_default or is_first:
        _set_default(db, p)
    db.commit()
    return out(p)


@router.patch("/{pid}", response_model=ProviderOut)
def update_provider(pid: str, body: ProviderPatch, db: Session = Depends(get_db)):
    p = _get(db, pid)
    if body.name is not None:
        p.name = body.name
    if body.base_url is not None:
        p.base_url = body.base_url.rstrip("/")
    if body.clear_api_key:
        p.api_key_enc = None
    elif body.api_key:
        p.api_key_enc = encrypt(body.api_key)
    if body.default_model is not None and body.default_model != p.default_model:
        p.default_model = body.default_model
        p.supports_tools = None  # re-test for the new model
    if body.params is not None:
        p.params = body.params.model_dump(exclude_none=True)
    if body.extra_headers is not None:
        p.extra_headers = body.extra_headers.model_dump(exclude_none=True)
    db.commit()
    return out(p)


@router.delete("/{pid}")
def delete_provider(pid: str, force: bool = False, db: Session = Depends(get_db)):
    p = _get(db, pid)
    others = db.query(Provider).filter(Provider.id != pid).count()
    if p.is_default and others:
        raise HTTPException(409, "This is the default provider. Make another provider the default first.")
    rt = get_runtime(db)
    uses = []
    if rt.generation_provider_id == pid:
        uses.append("semantic generation default")
    if rt.chat_provider_id == pid:
        uses.append("chat default")
    n_sessions = db.query(ChatSession).filter(ChatSession.provider_id == pid).count()
    if n_sessions:
        uses.append(f"{n_sessions} chat session(s)")
    if uses and not force:
        raise HTTPException(409, {"message": "Provider is in use", "in_use": uses})
    if rt.generation_provider_id == pid:
        rt.generation_provider_id, rt.generation_model = None, None
    if rt.chat_provider_id == pid:
        rt.chat_provider_id, rt.chat_model = None, None
    save_runtime(db, rt)
    db.delete(p)
    db.commit()
    return {"ok": True}


@router.post("/{pid}/default", response_model=ProviderOut)
def make_default(pid: str, db: Session = Depends(get_db)):
    p = _get(db, pid)
    _set_default(db, p)
    db.commit()
    return out(p)


class ProbeIn(BaseModel):
    type: ProviderType
    base_url: str
    api_key: Optional[str] = None
    provider_id: Optional[str] = None


@router.post("/probe-models")
def probe_models(body: ProbeIn, db: Session = Depends(get_db)):
    """List models for an unsaved form (P1-03). Falls back to suggestions on failure."""
    key = body.api_key
    if not key and body.provider_id:
        key = decrypt(_get(db, body.provider_id).api_key_enc)
    suggested = svc.provider_defaults().get(body.type, {}).get("suggested_models", [])
    try:
        models = svc.list_models(body.type, body.base_url, key, {})
        return {"ok": True, "models": models, "suggested": suggested}
    except (httpx.HTTPError, ValueError) as e:
        return {"ok": False, "error": str(e)[:300], "models": [{"id": m} for m in suggested], "suggested": suggested}


@router.post("/{pid}/refresh-models", response_model=ProviderOut)
def refresh_models(pid: str, db: Session = Depends(get_db)):
    p = _get(db, pid)
    try:
        p.available_models = svc.list_models(p.type, p.base_url, decrypt(p.api_key_enc), {})
    except (httpx.HTTPError, ValueError) as e:
        raise HTTPException(502, f"Could not list models: {e}") from e
    db.commit()
    return out(p)


class TestIn(BaseModel):
    model: Optional[str] = None


@router.post("/{pid}/test")
def test_connection(pid: str, body: TestIn = TestIn(), db: Session = Depends(get_db)):
    return svc.test_connection(db, _get(db, pid), body.model)


@router.post("/{pid}/test-tools")
def test_tools(pid: str, body: TestIn = TestIn(), db: Session = Depends(get_db)):
    return svc.test_tools(db, _get(db, pid), body.model)
