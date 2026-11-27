"""Product / inventory model."""

from sqlalchemy import Column, String, Float, Integer, Boolean, ForeignKey
from sqlalchemy.orm import relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKey


class Product(Base, UUIDPrimaryKey, TimestampMixin):
    """An inventory item in a branch."""

    __tablename__ = "products"

    branch_id           = Column(String, ForeignKey("branches.id", ondelete="CASCADE"), nullable=False)
    name                = Column(String, nullable=False)
    category            = Column(String)
    unit                = Column(String, default="unit")  # carton | bag | tin | piece
    cost_price          = Column(Float)
    sell_price          = Column(Float)
    quantity            = Column(Integer, default=0, nullable=False)
    low_stock_threshold = Column(Integer, default=5)
    is_deleted          = Column(Boolean, default=False, nullable=False)  # soft delete

    branch = relationship("Branch")
