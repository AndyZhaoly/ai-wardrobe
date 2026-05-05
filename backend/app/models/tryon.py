"""SQLAlchemy models for tryon_sessions and resale_listings."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text, Integer, JSON, TIMESTAMP, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.item import ClothingItem
    from app.models.user import User


class TryonSession(Base):
    __tablename__ = "tryon_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    person_image_path: Mapped[str] = mapped_column(String(500), nullable=False)
    garment_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clothing_items.id", ondelete="SET NULL"), nullable=True
    )
    result_image_path: Mapped[str | None] = mapped_column(String(500))
    clothing_category: Mapped[str | None] = mapped_column(String(50))  # upper_body | lower_body | dresses
    prompt: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    # status: pending | processing | done | error
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))

    user: Mapped[User] = relationship("User", back_populates="tryon_sessions", lazy="selectin")
    garment: Mapped[ClothingItem | None] = relationship("ClothingItem", lazy="selectin")


class ResaleListing(Base):
    __tablename__ = "resale_listings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clothing_items.id", ondelete="SET NULL"), nullable=True
    )
    garment_image_path: Mapped[str] = mapped_column(String(500), nullable=False)
    cropped_image_path: Mapped[str | None] = mapped_column(String(500))
    listing_title: Mapped[str | None] = mapped_column(String(50))
    listing_description: Mapped[str | None] = mapped_column(Text)
    original_price_cny: Mapped[int | None] = mapped_column(Integer)
    listing_price_usd: Mapped[int | None] = mapped_column(Integer)
    poshmark_category_path: Mapped[list | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    # status: draft | posted | error
    poshmark_listing_id: Mapped[str | None] = mapped_column(String(200))
    posted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship("User", back_populates="resale_listings", lazy="selectin")
    item: Mapped[ClothingItem | None] = relationship("ClothingItem", lazy="selectin")
