"""§5.3.3 task_items + §5.3.4 task_snapshots"""
from datetime import date as _date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Unicode,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base


class TaskItem(Base):
    __tablename__ = "task_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    aircraft_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("aircraft.id", ondelete="NO ACTION"), nullable=False
    )
    shop_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("shops.id", ondelete="NO ACTION"), nullable=False
    )
    work_package_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("work_packages.id", ondelete="NO ACTION"), nullable=True
    )
    assigned_supervisor_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="NO ACTION"), nullable=True
    )
    assigned_worker_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="NO ACTION"), nullable=True
    )
    distributed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    planned_mh: Mapped[Decimal | None] = mapped_column(
        Numeric(8, 2), nullable=True
    )
    task_text: Mapped[str] = mapped_column(Unicode, nullable=False)  # NVARCHAR(MAX)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("1")
    )
    deactivated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deactivated_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="NO ACTION"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("(GETUTCDATE())")
    )
    created_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="NO ACTION"), nullable=False
    )

    # Relationships
    aircraft = relationship("Aircraft", foreign_keys=[aircraft_id])
    shop = relationship("Shop", foreign_keys=[shop_id])
    work_package = relationship("WorkPackage", foreign_keys=[work_package_id])
    assigned_supervisor = relationship("User", foreign_keys=[assigned_supervisor_id])
    assigned_worker = relationship("User", foreign_keys=[assigned_worker_id])
    creator = relationship("User", foreign_keys=[created_by])
    snapshots = relationship(
        "TaskSnapshot", back_populates="task_item",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("idx_taskitem_shop", "shop_id"),
        Index("idx_taskitem_aircraft", "aircraft_id"),
        Index("idx_taskitem_active", "is_active"),
        Index("idx_taskitem_wp", "work_package_id", mssql_where=text("work_package_id IS NOT NULL")),
        Index("idx_task_items_supervisor", "assigned_supervisor_id", mssql_where=text("assigned_supervisor_id IS NOT NULL")),
        Index("idx_task_items_worker", "assigned_worker_id", mssql_where=text("assigned_worker_id IS NOT NULL")),
        Index("idx_task_items_distributed", "distributed_at", mssql_where=text("distributed_at IS NOT NULL")),
    )


class TaskSnapshot(Base):
    __tablename__ = "task_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("task_items.id", ondelete="CASCADE"), nullable=False
    )
    meeting_date: Mapped[_date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(
        Unicode(20), nullable=False, server_default=text("'NOT_STARTED'")
    )
    mh_incurred_hours: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, server_default=text("0")
    )
    remarks: Mapped[str | None] = mapped_column(Unicode, nullable=True)
    critical_issue: Mapped[str | None] = mapped_column(Unicode, nullable=True)
    has_issue: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("0")
    )
    deadline_date: Mapped[_date | None] = mapped_column(Date, nullable=True)
    correction_reason: Mapped[str | None] = mapped_column(Unicode, nullable=True)
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("0")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="NO ACTION"), nullable=True
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    supervisor_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("(GETUTCDATE())")
    )
    last_updated_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="NO ACTION"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("(GETUTCDATE())")
    )

    # Relationships
    task_item = relationship("TaskItem", back_populates="snapshots")
    deleter = relationship("User", foreign_keys=[deleted_by])
    updater = relationship("User", foreign_keys=[last_updated_by])

    __table_args__ = (
        CheckConstraint(
            "status IN ('NOT_STARTED','IN_PROGRESS','WAITING','COMPLETED')"
        ),
        UniqueConstraint("task_id", "meeting_date", name="uq_task_meeting"),
        Index("idx_snap_meeting_deleted", "meeting_date", "is_deleted"),
        Index("idx_snap_task", "task_id"),
    )

