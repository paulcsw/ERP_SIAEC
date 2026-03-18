"""Phase 3 ??MH Ledger tables (schema only, no MVP feature code)."""
from datetime import date as _date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Unicode,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class TimeLedgerDaily(Base):
    __tablename__ = "time_ledger_daily"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="NO ACTION"), nullable=False
    )
    date: Mapped[_date] = mapped_column("date", Date, nullable=False)
    capacity_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    planned_minutes_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_minutes_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    regular_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ot_minutes_actual: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ot_minutes_approved: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_regular: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    cost_ot: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    calc_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("(GETUTCDATE())")
    )

    __table_args__ = (
        UniqueConstraint("user_id", "date", name="uq_ledger_daily"),
    )


class LedgerAllocationDaily(Base):
    __tablename__ = "ledger_allocations_daily"

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
    allocated_regular_minutes: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    allocated_ot_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str | None] = mapped_column(
        Unicode(20), nullable=True, server_default=text("'PLANNED'")
    )

