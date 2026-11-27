"""Debt / credit model."""

from sqlalchemy import Column, String, Float, ForeignKey, Enum
from sqlalchemy.orm import relationship
import enum

from app.models.base import Base, UUIDPrimaryKey, TimestampMixin


class DebtStatus(str, enum.Enum):
    pending  = "pending"
    partial  = "partial"
    settled  = "settled"


class Debt(Base, UUIDPrimaryKey, TimestampMixin):
    """A credit sale or manually created debt record."""

    __tablename__ = "debts"

    branch_id      = Column(String, ForeignKey("branches.id"), nullable=False)
    customer_id    = Column(String, ForeignKey("customers.id"), nullable=True)
    customer_name  = Column(String)
    customer_phone = Column(String)
    sale_tx_id     = Column(String, ForeignKey("transactions.tx_id"), nullable=True)
    original_amount = Column(Float, nullable=False)
    amount_paid    = Column(Float, default=0.0)
    description    = Column(String)
    status         = Column(Enum(DebtStatus), default=DebtStatus.pending, nullable=False)

    branch      = relationship("Branch")
    customer    = relationship("Customer")
    transaction = relationship("Transaction")
