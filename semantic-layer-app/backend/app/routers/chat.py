import json
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..chat.orchestrator import TurnRunner
from ..db import get_db
from ..models import ChatMessage, ChatSession, DataSource, Provider, SemanticModelVersion
from ..services.providers import resolve_provider

router = APIRouter(prefix="/api/chat", tags=["chat"])


class SessionIn(BaseModel):
    source_id: str
    provider_id: Optional[str] = None
    model: Optional[str] = Field(None, max_length=300)
    # P4-13 test drive: chat against a specific (draft) version
    semantic_version_id: Optional[str] = None


class SessionPatch(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=300)
    provider_id: Optional[str] = None
    model: Optional[str] = Field(None, max_length=300)


def session_out(db: Session, s: ChatSession) -> Dict[str, Any]:
    src = db.get(DataSource, s.source_id)
    v = db.get(SemanticModelVersion, s.semantic_version_id)
    p = db.get(Provider, s.provider_id) if s.provider_id else None
    return {
        "id": s.id,
        "title": s.title,
        "source_id": s.source_id,
        "source_name": src.name if src else None,
        "semantic_version_id": s.semantic_version_id,
        "semantic_version": v.version if v else None,
        "newer_version_available": bool(
            src
            and src.active_semantic_version_id
            and src.active_semantic_version_id != s.semantic_version_id
            and not s.is_test
        ),
        "provider_id": s.provider_id,
        "provider_name": p.name if p else None,
        "supports_tools": p.supports_tools if p else None,
        "model": s.model,
        "is_test": s.is_test,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
    }


def message_out(m: ChatMessage) -> Dict[str, Any]:
    return {
        "id": m.id,
        "seq": m.seq,
        "role": m.role,
        "content": m.content if m.role != "tool" else "",
        "tool_calls": m.tool_calls,
        "tool_results": m.tool_results,
        "tokens_in": m.tokens_in,
        "tokens_out": m.tokens_out,
        "latency_ms": m.latency_ms,
        "created_at": m.created_at,
    }


def _session(db: Session, sid: str) -> ChatSession:
    s = db.get(ChatSession, sid)
    if s is None:
        raise HTTPException(404, "Chat session not found")
    return s


@router.get("/sessions")
def list_sessions(source_id: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(ChatSession).filter(ChatSession.is_test.is_(False))
    if source_id:
        q = q.filter(ChatSession.source_id == source_id)
    return [session_out(db, s) for s in q.order_by(ChatSession.updated_at.desc())]


@router.post("/sessions", status_code=201)
def create_session(body: SessionIn, db: Session = Depends(get_db)):
    src = db.get(DataSource, body.source_id)
    if src is None:
        raise HTTPException(404, "Data source not found")
    if body.semantic_version_id:
        v = db.get(SemanticModelVersion, body.semantic_version_id)
        if v is None or v.source_id != src.id:
            raise HTTPException(404, "Semantic version not found for this source")
        is_test = v.status == "draft"
    else:
        # P5 acceptance: only onboarded sources can be chatted with
        if src.status != "onboarded" or not src.active_semantic_version_id:
            raise HTTPException(409, "This source is not onboarded yet.")
        v, is_test = db.get(SemanticModelVersion, src.active_semantic_version_id), False
    try:
        provider, model = resolve_provider(db, "chat", body.provider_id, body.model)
    except LookupError as e:
        raise HTTPException(409, str(e)) from e
    s = ChatSession(
        source_id=src.id,
        semantic_version_id=v.id,
        provider_id=provider.id,
        model=model,
        is_test=is_test,
        title="Test drive" if is_test else "New chat",
    )
    db.add(s)
    db.commit()
    return session_out(db, s)


@router.get("/sessions/{sid}")
def get_session(sid: str, db: Session = Depends(get_db)):
    s = _session(db, sid)
    return {**session_out(db, s), "messages": [message_out(m) for m in s.messages]}


@router.patch("/sessions/{sid}")
def patch_session(sid: str, body: SessionPatch, db: Session = Depends(get_db)):
    s = _session(db, sid)
    if body.title is not None:
        s.title = body.title
    if body.provider_id is not None:
        if db.get(Provider, body.provider_id) is None:
            raise HTTPException(404, "Provider not found")
        s.provider_id = body.provider_id
    if body.model is not None:
        s.model = body.model
    db.commit()
    return session_out(db, s)


@router.delete("/sessions/{sid}")
def delete_session(sid: str, db: Session = Depends(get_db)):
    db.delete(_session(db, sid))
    db.commit()
    return {"ok": True}


def _sse(runner: TurnRunner) -> StreamingResponse:
    async def gen():
        async for ev in runner.run():
            yield f"event: {ev['type']}\ndata: {json.dumps(ev, default=str)}\n\n"

    return StreamingResponse(
        gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


class MessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=8000)


@router.post("/sessions/{sid}/messages")
def send_message(sid: str, body: MessageIn, db: Session = Depends(get_db)):
    """Streams SSE events: user_message, token, assistant_message, tool_call, tool_result, warning, error, done."""
    _session(db, sid)
    return _sse(TurnRunner(sid, body.content))


@router.post("/sessions/{sid}/retry")
def retry(sid: str, db: Session = Depends(get_db)):
    """P7-07: drop everything after the last user message and answer it again."""
    s = _session(db, sid)
    last_user = next((m for m in reversed(s.messages) if m.role == "user"), None)
    if last_user is None:
        raise HTTPException(409, "Nothing to retry.")
    for m in list(s.messages):
        if m.seq > last_user.seq:
            db.delete(m)
    db.commit()
    return _sse(TurnRunner(sid, None))


@router.get("/sessions/{sid}/export")
def export_markdown(sid: str, db: Session = Depends(get_db)):
    s = _session(db, sid)
    out = session_out(db, s)
    lines = [
        f"# {s.title}",
        "",
        f"Source: {out['source_name']} (semantic v{out['semantic_version']})"
        f" · Model: {out['provider_name']} / {s.model}",
        f"Exported: {datetime.utcnow().isoformat(timespec='seconds')}Z",
        "",
    ]
    for m in s.messages:
        if m.role == "user":
            lines += ["## You", "", m.content, ""]
        elif m.role == "assistant" and m.content:
            lines += ["## Assistant", "", m.content, ""]
        elif m.role == "tool":
            for r in m.tool_results:
                res = r.get("result") or {}
                lines += [
                    f"<details><summary>Tool: {r.get('name')}</summary>",
                    "",
                    "```json",
                    json.dumps(r.get("args"), indent=1),
                    "```",
                ]
                if res.get("sql"):
                    lines += ["```sql", res["sql"], "```"]
                lines += ["</details>", ""]
    name = "".join(ch if ch.isalnum() else "_" for ch in s.title)[:50] or "chat"
    return PlainTextResponse(
        "\n".join(lines),
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{name}.md"'},
    )
