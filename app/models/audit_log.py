"""Audit log — append-only record of every mutation."""

from sqlalchemy import Column, String, ForeignKey, JSON, DateTime
from app.models.base import Base, UUIDPrimaryKey, utcnow


class AuditLog(Base, UUIDPrimaryKey):
    """
    Immutable audit trail.
    Required on Business tier. Never deleted.
    """

    __tablename__ = "audit_logs"

    business_id  = Column(String, ForeignKey("businesses.id"), nullable=False)
    branch_id    = Column(String, ForeignKey("branches.id"), nullable=True)
    user_id      = Column(String, nullable=False)      # Supabase auth user UUID
    action       = Column(String, nullable=False)      # e.g. "sale.created", "product.updated"
    resource     = Column(String, nullable=False)      # e.g. "transactions", "products"
    resource_id  = Column(String, nullable=False)
    before_state = Column(JSON)
    after_state  = Column(JSON)
    created_at   = Column(DateTime(timezone=True), default=utcnow, nullable=False)
