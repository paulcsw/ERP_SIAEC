"""§5.3.2 user_shop_access"""
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    UniqueConstraint,
    Unicode,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base


class UserShopAccess(Base):
    __tablename__ = "user_shop_access"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="NO ACTION"), nullable=False
    )
    shop_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("shops.id", ondelete="NO ACTION"), nullable=False
    )
    access: Mapped[str] = mapped_column(Unicode(20), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("GETUTCDATE()")
    )
    granted_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="NO ACTION"), nullable=False
    )

    user = relationship("User", foreign_keys=[user_id])
    shop = relationship("Shop", foreign_keys=[shop_id])
    grantor = relationship("User", foreign_keys=[granted_by])

    __table_args__ = (
        CheckConstraint("access IN ('VIEW','EDIT','MANAGE')"),
        UniqueConstraint("user_id", "shop_id", name="uq_user_shop"),
    )
