"""Database tables. Change them through an Alembic migration (see alembic/versions/)."""

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import BigInteger, DateTime, Index, Integer, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Analysis(Base):
    """One menu rated by Gemini, kept so the same request can be answered from the cache."""

    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # sha256 of the menu link and requirements; see store.cache_key()
    cache_key: Mapped[str] = mapped_column(Text)
    menu_url: Mapped[str] = mapped_column(Text)
    preferences: Mapped[list[str]] = mapped_column(ARRAY(Text))
    other: Mapped[Optional[str]] = mapped_column(Text)
    # As Gemini returned them (after clean-up), so a cached result renders exactly like a fresh one.
    restaurant_name: Mapped[Optional[str]] = mapped_column(Text)
    location: Mapped[Optional[str]] = mapped_column(Text)
    dishes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)

    __table_args__ = (Index("ix_analyses_cache_key_created_at", "cache_key", "created_at"),)


class RateLimit(Base):
    """How many menu checks a key ("ip:<address>" or "daily") has used in one time window.

    Kept in the database so every copy of the app (Vercel can run several) shares the same counts.
    """

    __tablename__ = "rate_limits"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    hits: Mapped[int] = mapped_column(Integer)
