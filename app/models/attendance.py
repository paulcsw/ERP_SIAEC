"""Phase 2 ??Attendance tables (schema only, no MVP feature code)."""
from datetime import date as _date, datetime, time as _time

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Time,
    Unicode,
    UnicodeText,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class ShiftTemplate(Base):
    __tablename__ = "shift_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(Unicode(20), nullable=False, unique=True)
    start_time: Mapped[_time] = mapped_column(Time, nullable=False)
    end_time: Mapped[_time] = mapped_column(Time, nullable=False)
    paid_break_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("60")
    )


class ShiftAssignment(Base):
    __tablename__ = "shift_assignments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="NO ACTION"), nullable=False
    )
    shift_template_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("shift_templates.id", ondelete="NO ACTION"),
        nullable=False,
    )
    date: Mapped[_date] = mapped_column("date", Date, nullable=False)
    source: Mapped[str | None] = mapped_column(
        Unicode(20), nullable=True, server_default=text("'IMPORT'")
    )

    __table_args__ = (
        UniqueConstraint("user_id", "date", name="uq_shift_assignment"),
    )


class AttendanceEvent(Base):
    __tablename__ = "attendance_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="NO ACTION"), nullable=False
    )
    date: Mapped[_date] = mapped_column("date", Date, nullable=False)
    type: Mapped[str] = mapped_column("type", Unicode(30), nullable=False)
    start_time: Mapped[_time | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[_time | None] = mapped_column(Time, nullable=True)
    minutes_delta: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    approved_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="NO ACTION"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("(GETUTCDATE())")
    )

