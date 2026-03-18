from datetime import date as _date, datetime, time as _time

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Time,
    Unicode,
    UnicodeText,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class OtRequest(Base):
    __tablename__ = "ot_requests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="NO ACTION"), nullable=False
    )
    submitted_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="NO ACTION"), nullable=True
    )
    work_package_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("work_packages.id", ondelete="NO ACTION"),
        nullable=True,
    )
    shop_stream_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("shop_streams.id", ondelete="NO ACTION"),
        nullable=True,
    )
    date: Mapped[_date] = mapped_column("date", Date, nullable=False)
    start_time: Mapped[_time] = mapped_column(Time, nullable=False)
    end_time: Mapped[_time] = mapped_column(Time, nullable=False)
    requested_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    reason_code: Mapped[str] = mapped_column(
        Unicode(30), nullable=False, server_default=text("'OTHER'")
    )
    reason_text: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    status: Mapped[str] = mapped_column(
        Unicode(20), nullable=False, server_default=text("'PENDING'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("(GETUTCDATE())")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("(GETUTCDATE())")
    )

    __table_args__ = (
        CheckConstraint("requested_minutes > 0"),
        CheckConstraint(
            "reason_code IN ('BACKLOG','AOG','SCHEDULE_PRESSURE','MANPOWER_SHORTAGE','OTHER')"
        ),
        CheckConstraint(
            "status IN ('PENDING','ENDORSED','APPROVED','REJECTED','CANCELLED')"
        ),
        CheckConstraint("end_time > start_time", name="chk_ot_time"),
        Index("idx_ot_user_date", "user_id", "date"),
        Index("idx_ot_status", "status"),
        Index("idx_ot_date", "date"),
        Index("idx_ot_submitted_by", "submitted_by", mssql_where=text("submitted_by IS NOT NULL")),
    )


class OtApproval(Base):
    __tablename__ = "ot_approvals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ot_request_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("ot_requests.id", ondelete="NO ACTION"),
        nullable=False,
    )
    approver_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="NO ACTION"), nullable=False
    )
    stage: Mapped[str] = mapped_column(Unicode(20), nullable=False)
    action: Mapped[str] = mapped_column(Unicode(20), nullable=False)
    comment: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    acted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("(GETUTCDATE())")
    )

    __table_args__ = (
        CheckConstraint("stage IN ('ENDORSE','APPROVE')"),
        CheckConstraint("action IN ('APPROVE','REJECT')"),
        Index("idx_ota_request", "ot_request_id"),
        Index("idx_ota_approver", "approver_id"),
        Index("idx_ota_stage", "stage"),
    )

