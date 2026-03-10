"""Phase 3 — MH Tracking tables (schema only, no MVP feature code)."""
from datetime import date as _date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Time,
    Unicode,
    UnicodeText,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class DailyAssignment(Base):
    __tablename__ = "daily_assignments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="NO ACTION"), nullable=False
    )
    shop_stream_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("shop_streams.id", ondelete="NO ACTION"),
        nullable=False,
    )
    date: Mapped[_date] = mapped_column("date", Date, nullable=False)
    planned_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    assigned_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="NO ACTION"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("GETUTCDATE()")
    )

    __table_args__ = (
        CheckConstraint("planned_minutes >= 0"),
    )


class WorklogBlock(Base):
    __tablename__ = "worklog_blocks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="NO ACTION"), nullable=False
    )
    shop_stream_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("shop_streams.id", ondelete="NO ACTION"),
        nullable=False,
    )
    date: Mapped[_date] = mapped_column("date", Date, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ended_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("GETUTCDATE()")
    )

    __table_args__ = (
        CheckConstraint("minutes > 0"),
    )
