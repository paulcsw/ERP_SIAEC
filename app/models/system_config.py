from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Unicode,
    UnicodeText,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class SystemConfig(Base):
    __tablename__ = "system_config"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(
        "key", Unicode(100), nullable=False, unique=True
    )
    value: Mapped[str] = mapped_column(
        UnicodeText, nullable=False, server_default=text("''")
    )
    updated_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("GETUTCDATE()")
    )
