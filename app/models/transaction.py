"""Sales transaction models with idempotent tx_id."""

from sqlalchemy import Column, String, Float, Integer, ForeignKey, Enum, DateTime
from sqlalchemy.orm import relationship
import enum

from app.models.base import Base, TimestampMixin, UUIDPrimaryKey, utcnow


class PaymentMethod(str, enum.Enum):
    cash     = "cash"
    transfer = "transfer"
    pos      = "pos"
    credit   = "credit"


class TransactionStatus(str, enum.Enum):
    completed = "completed"
    voided    = "voided"
    pending   = "pending"


class Transaction(Base, TimestampMixin):
    """
    A sales transaction.

    tx_id is the CLIENT-generated UUID (idempotency key).
    INSERT ... ON CONFLICT (tx_id) DO NOTHING ensures offline sync safety.
    """

    __tablename__ = "transactions"

    # Client-generated idempotency key — primary key on this table
    tx_id = Column(String, primary_key=True)

    branch_id      = Column(String, ForeignKey("branches.id"), nullable=False)
    customer_id    = Column(String, ForeignKey("customers.id"), nullable=True)
    customer_name  = Column(String)                   # walk-in snapshot
    payment_method = Column(Enum(PaymentMethod), nullable=False)
    status         = Column(Enum(TransactionStatus), default=TransactionStatus.completed)
    total_amount   = Column(Float, nullable=False)
    notes          = Column(String)
    recorded_at    = Column(DateTime(timezone=True), default=utcnow)  # may differ from created_at for offline

    items    = relationship("TransactionItem", back_populates="transaction", cascade="all, delete-orphan")
    branch   = relationship("Branch")
    customer = relationship("Customer")


class TransactionItem(Base, UUIDPrimaryKey):
    """Line items within a transaction — snapshot of price at sale time."""

    __tablename__ = "transaction_items"

    transaction_tx_id = Column(String, ForeignKey("transactions.tx_id", ondelete="CASCADE"), nullable=False)
    product_id        = Column(String, ForeignKey("products.id"), nullable=False)
    product_name      = Column(String, nullable=False)   # snapshot — survives product deletion
    quantity          = Column(Integer, nullable=False)
    unit_price        = Column(Float, nullable=False)    # price at time of sale
    total             = Column(Float, nullable=False)    # unit_price * quantity

    transaction = relationship("Transaction", back_populates="items")
    product     = relationship("Product")
