"""SQLAlchemy ORM models for the metadata store (spec section 3)."""

import uuid
from datetime import datetime, timezone
from typing import Any, List, Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Provider(Base):
    __tablename__ = "providers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(32))  # openrouter | ollama | openai_compatible
    base_url: Mapped[str] = mapped_column(String(500))
    api_key_enc: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    default_model: Mapped[str] = mapped_column(String(300), default="")
    available_models: Mapped[List[Any]] = mapped_column(JSON, default=list)
    supports_tools: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    # P1-10: temperature, max_tokens, timeout_s, retries
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    # P1-11: OpenRouter HTTP-Referer / X-Title
    extra_headers: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class DataSource(Base):
    __tablename__ = "data_sources"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    type: Mapped[str] = mapped_column(String(32), default="csv")
    original_filename: Mapped[str] = mapped_column(String(500))
    storage_path: Mapped[str] = mapped_column(String(1000))
    parquet_path: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    parse_options: Mapped[dict] = mapped_column(JSON, default=dict)
    # sanitized column name -> original header (P2-06)
    column_map: Mapped[dict] = mapped_column(JSON, default=dict)
    row_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    column_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    # uploaded | profiled | draft_ready | in_review | onboarded | failed | archived
    status: Mapped[str] = mapped_column(String(32), default="uploaded")
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ingest_report: Mapped[dict] = mapped_column(JSON, default=dict)
    active_semantic_version_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    profile: Mapped[Optional["Profile"]] = relationship(
        back_populates="source", cascade="all, delete-orphan", uselist=False
    )
    versions: Mapped[List["SemanticModelVersion"]] = relationship(
        back_populates="source",
        cascade="all, delete-orphan",
        order_by="SemanticModelVersion.version",
    )


class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(ForeignKey("data_sources.id", ondelete="CASCADE"), unique=True)
    data: Mapped[dict] = mapped_column(JSON)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    source: Mapped[DataSource] = relationship(back_populates="profile")


class SemanticModelVersion(Base):
    __tablename__ = "semantic_models"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(ForeignKey("data_sources.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft|approved|superseded
    origin: Mapped[str] = mapped_column(String(16), default="ai")  # ai|user|ai+user
    model_json: Mapped[dict] = mapped_column(JSON)
    # The baseline the review diff is computed against (the AI draft, or the version it was copied from)
    base_json: Mapped[dict] = mapped_column(JSON)
    generated_by_provider: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    generated_by_model: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    prompt_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    change_notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    source: Mapped[DataSource] = relationship(back_populates="versions")


class Job(Base):
    """Background jobs (P3-10). Only semantic generation for now."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(32))
    source_id: Mapped[Optional[str]] = mapped_column(ForeignKey("data_sources.id", ondelete="CASCADE"), nullable=True)
    # pending | running | succeeded | failed | cancelled
    status: Mapped[str] = mapped_column(String(16), default="pending")
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    progress: Mapped[List[Any]] = mapped_column(JSON, default=list)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    result_version_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(ForeignKey("data_sources.id", ondelete="CASCADE"))
    semantic_version_id: Mapped[str] = mapped_column(String(32))
    provider_id: Mapped[Optional[str]] = mapped_column(ForeignKey("providers.id", ondelete="SET NULL"), nullable=True)
    model: Mapped[str] = mapped_column(String(300), default="")
    title: Mapped[str] = mapped_column(String(300), default="New chat")
    # P4-13 test-drive sessions run against a draft and are hidden from the chat list
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    messages: Mapped[List["ChatMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="ChatMessage.seq"
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))  # user | assistant | tool
    content: Mapped[str] = mapped_column(Text, default="")
    tool_calls: Mapped[List[Any]] = mapped_column(JSON, default=list)
    # For role=tool: [{tool_call_id, name, result}] -- the full result object shown in the UI
    tool_results: Mapped[List[Any]] = mapped_column(JSON, default=list)
    tokens_in: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    session: Mapped[ChatSession] = relationship(back_populates="messages")


class LLMCall(Base):
    """P8-03 usage log. `payload` is only filled in debug mode (P8-04)."""

    __tablename__ = "llm_calls"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    purpose: Mapped[str] = mapped_column(String(32))  # generation | chat | test
    provider_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    provider_name: Mapped[str] = mapped_column(String(200), default="")
    model: Mapped[str] = mapped_column(String(300), default="")
    tokens_in: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    payload: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
