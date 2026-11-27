"""Business and Branch models."""

from sqlalchemy import Column, String, ForeignKey, Enum
from sqlalchemy.orm import relationship
import enum

from app.models.base import Base, TimestampMixin, UUIDPrimaryKey


class PlanTier(str, enum.Enum):
    free     = "free"
    starter  = "starter"
    growth   = "growth"
    business = "business"


class Business(Base, UUIDPrimaryKey, TimestampMixin):
    """Top-level tenant. Every resource belongs to a business."""

    __tablename__ = "businesses"

    name          = Column(String, nullable=False)
    phone         = Column(String, nullable=False, unique=True)
    address       = Column(String)
    business_type = Column(String)  # provision_store | pharmacy | electronics | etc.
    plan          = Column(Enum(PlanTier), default=PlanTier.free, nullable=False)
    owner_id      = Column(String, nullable=False)  # Supabase auth user UUID

    branches  = relationship("Branch", back_populates="business", cascade="all, delete-orphan")


class Branch(Base, UUIDPrimaryKey, TimestampMixin):
    """A physical location belonging to a business."""

    __tablename__ = "branches"

    business_id = Column(String, ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    name        = Column(String, nullable=False)
    address     = Column(String)

    business = relationship("Business", back_populates="branches")
