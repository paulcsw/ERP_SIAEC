from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Table,
    Unicode,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base

# §5.2 user_roles (association table ??composite PK, no extra columns)
user_roles = Table(
    "user_roles",
    Base.metadata,
    Column(
        "user_id",
        BigInteger,
        ForeignKey("users.id", ondelete="NO ACTION"),
        primary_key=True,
    ),
    Column(
        "role_id",
        Integer,
        ForeignKey("roles.id", ondelete="NO ACTION"),
        primary_key=True,
    ),
)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    employee_no: Mapped[str] = mapped_column(Unicode(20), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Unicode(100), nullable=False)
    email: Mapped[str | None] = mapped_column(Unicode(255), nullable=True)
    team: Mapped[str | None] = mapped_column(Unicode(50), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("1")
    )
    azure_oid: Mapped[str | None] = mapped_column(Unicode(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("(GETUTCDATE())")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("(GETUTCDATE())")
    )

    roles: Mapped[list["Role"]] = relationship(
        secondary=user_roles, back_populates="users"
    )

    __table_args__ = (
        Index("uq_users_email_not_null", "email", unique=True, mssql_where=text("email IS NOT NULL")),
        Index("uq_users_azure_oid_not_null", "azure_oid", unique=True, mssql_where=text("azure_oid IS NOT NULL")),
        Index("idx_users_team", "team"),
    )


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Unicode(20), nullable=False, unique=True)

    users: Mapped[list["User"]] = relationship(
        secondary=user_roles, back_populates="roles"
    )

    __table_args__ = (
        CheckConstraint("name IN ('WORKER', 'SUPERVISOR', 'ADMIN')"),
    )

