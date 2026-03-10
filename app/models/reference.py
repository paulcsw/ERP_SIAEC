from datetime import date as _date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    Unicode,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class Aircraft(Base):
    __tablename__ = "aircraft"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ac_reg: Mapped[str] = mapped_column(Unicode(20), nullable=False, unique=True)
    airline: Mapped[str | None] = mapped_column(Unicode(100), nullable=True)
    status: Mapped[str] = mapped_column(
        Unicode(20), nullable=False, server_default=text("'ACTIVE'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("GETUTCDATE()")
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE', 'COMPLETED', 'ON_HOLD', 'CANCELLED')"
        ),
    )


class WorkPackage(Base):
    __tablename__ = "work_packages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    aircraft_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("aircraft.id", ondelete="NO ACTION"), nullable=False
    )
    rfo_no: Mapped[str | None] = mapped_column(Unicode(50), nullable=True)
    title: Mapped[str] = mapped_column(Unicode(200), nullable=False)
    start_date: Mapped[_date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[_date | None] = mapped_column(Date, nullable=True)
    priority: Mapped[int | None] = mapped_column(
        SmallInteger, nullable=True, server_default=text("0")
    )
    status: Mapped[str] = mapped_column(
        Unicode(20), nullable=False, server_default=text("'ACTIVE'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("GETUTCDATE()")
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE', 'COMPLETED', 'ON_HOLD', 'CANCELLED')"
        ),
        Index("uq_wp_rfo_no_not_null", "rfo_no", unique=True, mssql_where=text("rfo_no IS NOT NULL")),
        Index("idx_wp_aircraft", "aircraft_id"),
        Index("idx_wp_status", "status"),
    )


class ShopStream(Base):
    __tablename__ = "shop_streams"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    work_package_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("work_packages.id", ondelete="NO ACTION"),
        nullable=False,
    )
    shop_code: Mapped[str] = mapped_column(Unicode(20), nullable=False)
    status: Mapped[str] = mapped_column(
        Unicode(20), nullable=False, server_default=text("'ACTIVE'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("GETUTCDATE()")
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE', 'COMPLETED', 'ON_HOLD', 'CANCELLED')"
        ),
        UniqueConstraint("work_package_id", "shop_code", name="uq_shop_stream"),
        Index("idx_ss_wp", "work_package_id"),
    )
