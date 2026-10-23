"""Structured JSON logging with per-request IDs (P0-10)."""

import contextvars
import json
import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from .config import settings

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        extra = getattr(record, "extra_fields", None)
        if extra:
            out.update(extra)
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def setup_logging() -> None:
    handler = logging.StreamHandler()
    if settings.log_json:
        handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level)
    for noisy in ("httpx", "httpcore", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def log(logger: logging.Logger, level: int, msg: str, **fields) -> None:
    logger.log(level, msg, extra={"extra_fields": fields})


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        token = request_id_var.set(rid)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["x-request-id"] = rid
        log(
            logging.getLogger("http"),
            logging.INFO,
            "request",
            request_id=rid,
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            ms=round((time.perf_counter() - start) * 1000),
        )
        return response
