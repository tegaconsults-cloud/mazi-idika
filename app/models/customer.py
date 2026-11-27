"""Customer model."""

from sqlalchemy import Column, String, ForeignKey
from sqlalchemy.orm import relationship

from app.models.base import Base, UUIDPrimaryKey, TimestampMixin


class Customer(Base, UUIDPrimaryKey, TimestampMixin):
    """A named customer of a business."""

    __tablename__ = "customers"

    business_id = Column(String, ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False)
    name        = Column(String, nullable=False)
    phone       = Column(String)
    notes       = Column(String)

    business = relationship("Business")
