"""
Batch and report records — now persisted in Postgres rather than a process dict.

A bulk change is an audit trail: who changed what, when, and whether it was a
preview or a commit. That should survive a restart.
"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import func
from sqlmodel import Session, delete, select

from ..domain.models import BatchRecord, ReportRecord


class ArtifactNotFound(Exception):
    def __init__(self, artifact_id: str):
        self.artifact_id = artifact_id
        super().__init__(f"No artifact with id {artifact_id!r}")


def _next_id(session: Session, model, prefix: str) -> str:
    highest = 0
    for existing in session.exec(select(model.id)).all():
        digits = "".join(ch for ch in existing if ch.isdigit())
        if digits:
            highest = max(highest, int(digits))
    return f"{prefix}-{highest + 1:04d}"


def save_batch(session: Session, **fields: Any) -> BatchRecord:
    record = BatchRecord(id=_next_id(session, BatchRecord, "BATCH"), **fields)
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def get_batch(session: Session, batch_id: str) -> BatchRecord:
    record = session.get(BatchRecord, batch_id)
    if record is None:
        raise ArtifactNotFound(batch_id)
    return record


def list_batches(session: Session, limit: int = 20) -> list[BatchRecord]:
    return list(
        session.exec(
            select(BatchRecord).order_by(BatchRecord.created_at.desc()).limit(limit)
        ).all()
    )


def save_report(session: Session, **fields: Any) -> ReportRecord:
    record = ReportRecord(id=_next_id(session, ReportRecord, "RPT"), **fields)
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def get_report(session: Session, report_id: str) -> ReportRecord:
    record = session.get(ReportRecord, report_id)
    if record is None:
        raise ArtifactNotFound(report_id)
    return record


def list_reports(session: Session, limit: int = 20) -> list[ReportRecord]:
    return list(
        session.exec(
            select(ReportRecord).order_by(ReportRecord.created_at.desc()).limit(limit)
        ).all()
    )


def clear(session: Session) -> None:
    session.exec(delete(BatchRecord))
    session.exec(delete(ReportRecord))
    session.commit()


def counts(session: Session) -> dict[str, int]:
    return {
        "batches": int(session.scalar(select(func.count()).select_from(BatchRecord)) or 0),
        "reports": int(session.scalar(select(func.count()).select_from(ReportRecord)) or 0),
    }
