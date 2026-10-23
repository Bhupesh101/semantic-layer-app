"""Chat agent loop with tool calling and SSE events (P7-08..P7-16)."""

import asyncio
import json
import logging
import re
import time
from datetime import date
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

import openai
from starlette.concurrency import run_in_threadpool

from ..db import SessionLocal
from ..logging_setup import log
from ..models import ChatMessage, ChatSession, DataSource, Provider, SemanticModelVersion
from ..semantic.schema import SemanticModel
from ..services.llm_log import record_call
from ..services.providers import async_client, effective_params, resolve_provider
from ..services.runtime_settings import get_runtime
from .tools import ToolContext, compact_model, run_tool, tool_schemas

PROMPT_VERSION = "chat_system_v1"
PROMPT_FILE = Path(__file__).resolve().parent.parent / "prompts" / f"{PROMPT_VERSION}.md"
logger = logging.getLogger(__name__)

REACT_PROTOCOL = """
TOOL PROTOCOL (your model has no native tool calling)
To call tools, reply with ONLY a JSON object and nothing else:
{"tool_calls": [{"name": "<tool name>", "arguments": {...}}]}
Available tools and their JSON-schema arguments:
{tools}
After you receive results, either call more tools the same way or reply with the final answer as plain text (not JSON).
"""


def build_system_prompt(model: SemanticModel, profile: Dict[str, Any]) -> str:
    return (
        PROMPT_FILE.read_text()
        .replace("{today}", date.today().isoformat())
        .replace("{model}", json.dumps(compact_model(model, profile), indent=1, default=str))
    )


def _approx_tokens(obj: Any) -> int:
    return len(json.dumps(obj, default=str)) // 4


def history_messages(rows: List[ChatMessage], native: bool) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for m in rows:
        if m.role == "user":
            out.append({"role": "user", "content": m.content})
        elif m.role == "assistant":
            if native:
                msg: Dict[str, Any] = {"role": "assistant", "content": m.content or None}
                if m.tool_calls:
                    msg["tool_calls"] = [
                        {
                            "id": c["id"],
                            "type": "function",
                            "function": {"name": c["name"], "arguments": json.dumps(c.get("arguments") or {})},
                        }
                        for c in m.tool_calls
                    ]
                if msg["content"] is None and "tool_calls" not in msg:
                    msg["content"] = ""
                out.append(msg)
            else:
                content = m.content
                if m.tool_calls:
                    content = json.dumps(
                        {
                            "tool_calls": [
                                {"name": c["name"], "arguments": c.get("arguments") or {}} for c in m.tool_calls
                            ]
                        }
                    )
                out.append({"role": "assistant", "content": content or ""})
        elif m.role == "tool":
            if native:
                out.append({"role": "tool", "tool_call_id": m.tool_results[0]["tool_call_id"], "content": m.content})
            else:
                name = m.tool_results[0]["name"] if m.tool_results else "tool"
                out.append({"role": "user", "content": f"RESULT of {name}:\n{m.content}"})
    if not native:
        # merge consecutive user messages (several tool results in a row)
        merged: List[Dict[str, Any]] = []
        for msg in out:
            if merged and merged[-1]["role"] == msg["role"] == "user":
                merged[-1]["content"] += "\n\n" + msg["content"]
            else:
                merged.append(msg)
        out = merged
    return out


def trim_history(msgs: List[Dict[str, Any]], budget: int) -> "tuple[List[Dict[str, Any]], bool]":
    """Drop the oldest whole turns (from a user message to the next) until under budget (P7-14)."""
    turns: List[List[Dict[str, Any]]] = []
    for m in msgs:
        if m["role"] == "user" and not str(m.get("content", "")).startswith("RESULT of ") or not turns:
            turns.append([m])
        else:
            turns[-1].append(m)
    trimmed = False
    while len(turns) > 1 and sum(_approx_tokens(t) for t in turns) > budget:
        turns.pop(0)
        trimmed = True
    return [m for t in turns for m in t], trimmed


def parse_react(text: str) -> Optional[List[Dict[str, Any]]]:
    """Find {"tool_calls": [...]} in a fallback-mode reply. None = it's a final answer."""
    candidate = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", candidate, re.S)
    if fence:
        candidate = fence.group(1)
    if not candidate.startswith("{"):
        start = candidate.find('{"tool_calls"')
        if start < 0:
            return None
        candidate = candidate[start:]
    try:
        obj, _ = json.JSONDecoder().raw_decode(candidate)
    except json.JSONDecodeError:
        return None
    calls = obj.get("tool_calls") if isinstance(obj, dict) else None
    if not isinstance(calls, list) or not calls:
        return None
    return [{"name": str(c.get("name")), "arguments": c.get("arguments") or {}} for c in calls if isinstance(c, dict)]


class TurnRunner:
    """Runs one user turn. Yields event dicts; the router turns them into SSE frames."""

    def __init__(self, session_id: str, user_text: Optional[str]):
        self.session_id = session_id
        self.user_text = user_text
        self.db = SessionLocal()
        self.partial = ""

    def _next_seq(self) -> int:
        last = (
            self.db.query(ChatMessage.seq)
            .filter(ChatMessage.session_id == self.session_id)
            .order_by(ChatMessage.seq.desc())
            .first()
        )
        return (last[0] + 1) if last else 1

    def _save(self, **kw) -> ChatMessage:
        m = ChatMessage(session_id=self.session_id, seq=self._next_seq(), **kw)
        self.db.add(m)
        self.db.commit()
        return m

    async def run(self) -> AsyncIterator[Dict[str, Any]]:
        try:
            async for ev in self._run():
                yield ev
        except (asyncio.CancelledError, GeneratorExit):
            # P7-07 stop: keep whatever was streamed so far
            if self.partial:
                self._save(role="assistant", content=self.partial + "\n\n_[stopped]_")
            raise
        except Exception as e:  # noqa: BLE001
            log(logger, logging.ERROR, "chat turn failed", session_id=self.session_id, error=str(e))
            yield {"type": "error", "message": _friendly(e)}
        finally:
            self.db.close()

    async def _run(self) -> AsyncIterator[Dict[str, Any]]:
        db = self.db
        s = db.get(ChatSession, self.session_id)
        if s is None:
            yield {"type": "error", "message": "Chat session not found."}
            return
        source = db.get(DataSource, s.source_id)
        version = db.get(SemanticModelVersion, s.semantic_version_id)
        if source is None or version is None or not source.parquet_path:
            yield {"type": "error", "message": "The data source or its semantic model is no longer available."}
            return
        rt = get_runtime(db)
        provider = db.get(Provider, s.provider_id) if s.provider_id else None
        if provider is None:
            provider, s.model = resolve_provider(db, "chat")
            s.provider_id = provider.id
            db.commit()
        model_id = s.model or provider.default_model
        native = provider.supports_tools is not False
        if not native:
            yield {
                "type": "warning",
                "message": "This model has no native tool calling; using a text protocol. "
                "Answers may be less reliable.",
            }
        if source.active_semantic_version_id and source.active_semantic_version_id != version.id and not s.is_test:
            yield {
                "type": "warning",
                "message": "A newer semantic model version exists. This chat stays on the version it started with.",
            }

        if self.user_text is not None:
            u = self._save(role="user", content=self.user_text)
            yield {"type": "user_message", "id": u.id}
            if s.title == "New chat":
                s.title = self.user_text.strip().splitlines()[0][:60] or "New chat"
                db.commit()

        sem = SemanticModel.model_validate(version.model_json)
        profile = source.profile.data
        ctx = ToolContext(sem, profile, Path(source.parquet_path), rt)
        tools = tool_schemas(rt)
        system = build_system_prompt(sem, profile)
        if not native:
            system += REACT_PROTOCOL.replace("{tools}", json.dumps([t["function"] for t in tools], indent=1))
        history = history_messages(list(s.messages), native)
        history, trimmed = trim_history(history, rt.context_budget_tokens - _approx_tokens(system))
        if trimmed:
            system += "\n\n(Older turns of this conversation were omitted to fit the context window.)"
        messages: List[Dict[str, Any]] = [{"role": "system", "content": system}, *history]

        prm = effective_params(provider)
        aclient = async_client(provider)
        deadline = time.monotonic() + rt.agent_timeout_s

        for iteration in range(rt.max_agent_iterations + 1):
            final_round = iteration == rt.max_agent_iterations or time.monotonic() > deadline
            if final_round:
                messages.append(
                    {
                        "role": "user",
                        "content": "Tool budget exhausted. Answer now with the results you have, "
                        "and say what is missing.",
                    }
                )
            started = time.perf_counter()
            self.partial = ""
            if native:
                content, calls, usage = "", {}, None
                kwargs: Dict[str, Any] = dict(
                    model=model_id,
                    messages=messages,
                    stream=True,
                    temperature=float(prm["temperature"]),
                    max_tokens=int(prm["max_tokens"]),
                )
                # Tools stay declared on the final round because history contains tool calls
                kwargs["tools"] = tools
                if final_round:
                    kwargs["tool_choice"] = "none"
                try:
                    stream = await aclient.chat.completions.create(stream_options={"include_usage": True}, **kwargs)
                except openai.BadRequestError:
                    stream = await aclient.chat.completions.create(**kwargs)
                async for chunk in stream:
                    if getattr(chunk, "usage", None):
                        usage = chunk.usage
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    if delta.content:
                        content += delta.content
                        self.partial = content
                        yield {"type": "token", "text": delta.content}
                    for tc in delta.tool_calls or []:
                        slot = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                        if tc.id:
                            slot["id"] = tc.id
                        if tc.function and tc.function.name:
                            slot["name"] += tc.function.name
                        if tc.function and tc.function.arguments:
                            slot["arguments"] += tc.function.arguments
                tool_calls = []
                for i, c in sorted(calls.items()):
                    try:
                        args = json.loads(c["arguments"] or "{}")
                    except json.JSONDecodeError:
                        args = {"__raw__": c["arguments"]}
                    tool_calls.append({"id": c["id"] or f"call_{iteration}_{i}", "name": c["name"], "arguments": args})
            else:
                resp = await aclient.chat.completions.create(
                    model=model_id,
                    messages=messages,
                    temperature=float(prm["temperature"]),
                    max_tokens=int(prm["max_tokens"]),
                )
                usage = resp.usage
                text = resp.choices[0].message.content or ""
                parsed = None if final_round else parse_react(text)
                if parsed:
                    content = ""
                    tool_calls = [{"id": f"call_{iteration}_{i}", **c} for i, c in enumerate(parsed)]
                else:
                    content, tool_calls = text, []
                    yield {"type": "token", "text": text}
            latency = round((time.perf_counter() - started) * 1000)
            record_call(db, "chat", provider, model_id, usage, latency, payload=messages if rt.debug_payloads else None)
            self.partial = ""
            a = self._save(
                role="assistant",
                content=content,
                tool_calls=tool_calls,
                tokens_in=getattr(usage, "prompt_tokens", None),
                tokens_out=getattr(usage, "completion_tokens", None),
                latency_ms=latency,
            )
            yield {
                "type": "assistant_message",
                "id": a.id,
                "has_tool_calls": bool(tool_calls),
                "tokens_in": a.tokens_in,
                "tokens_out": a.tokens_out,
                "latency_ms": latency,
            }

            if native:
                msg: Dict[str, Any] = {"role": "assistant", "content": content or None}
                if tool_calls:
                    msg["tool_calls"] = [
                        {
                            "id": c["id"],
                            "type": "function",
                            "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])},
                        }
                        for c in tool_calls
                    ]
                messages.append(msg)
            else:
                messages.append(
                    {
                        "role": "assistant",
                        "content": content
                        or json.dumps(
                            {"tool_calls": [{"name": c["name"], "arguments": c["arguments"]} for c in tool_calls]}
                        ),
                    }
                )

            if not tool_calls:
                yield {"type": "done"}
                return

            for c in tool_calls:
                yield {"type": "tool_call", "id": c["id"], "name": c["name"], "arguments": c["arguments"]}
            # P7-10: run parallel tool calls concurrently
            results = await asyncio.gather(
                *[run_in_threadpool(run_tool, ctx, c["name"], c["arguments"]) for c in tool_calls]
            )
            react_parts = []
            for c, r in zip(tool_calls, results):
                llm_content = json.dumps(r["llm"], default=str)
                self._save(
                    role="tool",
                    content=llm_content,
                    tool_results=[
                        {
                            "tool_call_id": c["id"],
                            "name": c["name"],
                            "ok": r["ok"],
                            "args": r["args"],
                            "result": r["ui"],
                        }
                    ],
                )
                yield {"type": "tool_result", "id": c["id"], "name": c["name"], "ok": r["ok"], "result": r["ui"]}
                if native:
                    messages.append({"role": "tool", "tool_call_id": c["id"], "content": llm_content})
                else:
                    react_parts.append(f"RESULT of {c['name']}:\n{llm_content}")
            if react_parts:
                messages.append({"role": "user", "content": "\n\n".join(react_parts)})
        yield {"type": "done"}


def _friendly(e: Exception) -> str:
    if isinstance(e, LookupError):
        return str(e)
    if isinstance(e, openai.AuthenticationError):
        return "The provider rejected the API key. Check it on the Providers page."
    if isinstance(e, openai.NotFoundError):
        return f"The provider could not find the model: {getattr(e, 'message', e)}"
    if isinstance(e, openai.APIConnectionError):
        return "Could not reach the LLM provider. Is it running and is the base URL correct?"
    if isinstance(e, openai.RateLimitError):
        return "The provider is rate-limiting requests. Try again shortly."
    if isinstance(e, openai.APIStatusError):
        return f"Provider error ({e.status_code}): {getattr(e, 'message', e)}"
    return f"Something went wrong: {e}"
