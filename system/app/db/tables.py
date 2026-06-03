"""ORM tables.

Strategy: dedicated tables for the high-traffic / heavily-queried entities
(departments, tasks, messages, activity, cost, memory, graph, jobs) and a
single generic `entities` table for the long tail of v0.4 record types
(artifacts, decisions, bulletins, projects, skills, …). Each dedicated table
keeps the full API object in a `data` JSON column plus a few indexed columns
for the axes we actually filter on (dept, status, project, ts).
"""
from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, JSONFlex


class Company(Base):
    __tablename__ = "company"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    company_name: Mapped[str] = mapped_column(String(120))
    running: Mapped[bool] = mapped_column(Boolean, default=True)
    daily_cap_usd: Mapped[float] = mapped_column(Float, default=500.0)
    created_at: Mapped[int] = mapped_column(BigInteger)
    data: Mapped[dict] = mapped_column(JSONFlex, default=dict)


class Department(Base):
    __tablename__ = "departments"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[int] = mapped_column(BigInteger, index=True)
    data: Mapped[dict] = mapped_column(JSONFlex)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    department_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    created_at: Mapped[int] = mapped_column(BigInteger, index=True)
    updated_at: Mapped[int] = mapped_column(BigInteger, index=True)
    data: Mapped[dict] = mapped_column(JSONFlex)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(96), index=True)
    ts: Mapped[int] = mapped_column(BigInteger, index=True)
    data: Mapped[dict] = mapped_column(JSONFlex)


class Activity(Base):
    __tablename__ = "activity"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ts: Mapped[int] = mapped_column(BigInteger, index=True)
    department_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    data: Mapped[dict] = mapped_column(JSONFlex)


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    ts: Mapped[int] = mapped_column(BigInteger, index=True)
    data: Mapped[dict] = mapped_column(JSONFlex)


class Objective(Base):
    __tablename__ = "objectives"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    data: Mapped[dict] = mapped_column(JSONFlex)


class CostRecordRow(Base):
    __tablename__ = "cost_records"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ts: Mapped[int] = mapped_column(BigInteger, index=True)
    department_id: Mapped[str] = mapped_column(String(64), index=True)
    category: Mapped[str] = mapped_column(String(16))
    usd: Mapped[float] = mapped_column(Float)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    # provenance for the finance analytics (§28)
    provider_id: Mapped[str | None] = mapped_column(String(24), nullable=True)
    model: Mapped[str | None] = mapped_column(String(48), nullable=True)
    speed: Mapped[str | None] = mapped_column(String(16), nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)


Index("ix_cost_dept_ts", CostRecordRow.department_id, CostRecordRow.ts)


class MemoryArchive(Base):
    __tablename__ = "memory_archive"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    department_id: Mapped[str] = mapped_column(String(64), index=True)
    ts: Mapped[int] = mapped_column(BigInteger, index=True)
    data: Mapped[dict] = mapped_column(JSONFlex)


class MemoryKnowledge(Base):
    __tablename__ = "memory_knowledge"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    department_id: Mapped[str] = mapped_column(String(64), index=True)
    ts: Mapped[int] = mapped_column(BigInteger, index=True)
    title: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    score: Mapped[float] = mapped_column(Float, default=0.7)
    tags: Mapped[list] = mapped_column(JSONFlex, default=list)
    embedding: Mapped[list | None] = mapped_column(JSONFlex, nullable=True)
    embedding_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    embedding_dim: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding_ts: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)  # provenance (§17.4)


class GraphNode(Base):
    __tablename__ = "graph_nodes"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    department_id: Mapped[str] = mapped_column(String(64), index=True)
    label: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String(24))
    x: Mapped[float] = mapped_column(Float, default=0.5)
    y: Mapped[float] = mapped_column(Float, default=0.5)
    valid_from: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    valid_to: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.7)
    source: Mapped[str | None] = mapped_column(Text, nullable=True)


class GraphEdge(Base):
    __tablename__ = "graph_edges"
    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    department_id: Mapped[str] = mapped_column(String(64), index=True)
    from_id: Mapped[str] = mapped_column(String(96))
    to_id: Mapped[str] = mapped_column(String(96))
    rel: Mapped[str] = mapped_column(String(120))
    valid_from: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    valid_to: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.7)
    source: Mapped[str | None] = mapped_column(Text, nullable=True)


class Job(Base):
    """Durable work queue — survives restart so the company keeps running 24/7."""
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(48), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)  # queued|running|done|failed|cancelled
    run_after: Mapped[int] = mapped_column(BigInteger, index=True)
    priority: Mapped[int] = mapped_column(Integer, default=5)
    payload: Mapped[dict] = mapped_column(JSONFlex, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[int] = mapped_column(BigInteger, index=True)


Index("ix_jobs_ready", Job.status, Job.run_after)


class Entity(Base):
    """Generic store for the v0.4 long-tail record types.

    `type` is the discriminator (artifact, decision, bulletin, project, skill,
    preference, trigger, meeting, playbook, lesson, critique, evidence,
    war_room, notification, handoff_message, visibility_policy,
    artifact_version, …). `data` holds the full API object.
    """
    __tablename__ = "entities"
    type: Mapped[str] = mapped_column(String(40), primary_key=True)
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    dept: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    project: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    status: Mapped[str | None] = mapped_column(String(24), index=True, nullable=True)
    ts: Mapped[int] = mapped_column(BigInteger, index=True, default=0)
    data: Mapped[dict] = mapped_column(JSONFlex)


Index("ix_entities_type_ts", Entity.type, Entity.ts)
