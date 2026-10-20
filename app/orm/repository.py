"""Handwritten repository/service layer over the GENERATED SQLAlchemy models (PostgreSQL only)."""
from __future__ import annotations
from sqlalchemy import select, create_engine
from sqlalchemy.orm import Session, selectinload

def make_engine(url: str):
    return create_engine(url, pool_pre_ping=True)

class Repository:
    def __init__(self, session: Session, model):
        self.s, self.model = session, model
    def create(self, **values):
        obj = self.model(**values); self.s.add(obj); self.s.flush(); return obj
    def get(self, pk):
        return self.s.get(self.model, pk)
    def list(self, limit: int = 100):
        return list(self.s.scalars(select(self.model).order_by(self.model.id).limit(limit)))
    def update(self, pk, **values):
        obj = self.get(pk)
        if obj is None: raise LookupError(f"{self.model.__name__} {pk!r} not found")
        for k, v in values.items():
            if not hasattr(self.model, k): raise AttributeError(f"{self.model.__name__} has no column {k!r}")
            setattr(obj, k, v)
        self.s.flush(); return obj
    def delete(self, pk) -> bool:
        obj = self.get(pk)
        if obj is None: return False
        self.s.delete(obj); self.s.flush(); return True

class OrderService:
    """Reads orders together with their line items (the former embedded array)."""
    def __init__(self, session: Session, models):
        self.s, self.m = session, models
    def orders_with_items(self, customer_id: str | None = None, limit: int = 20):
        stmt = select(self.m.Order).options(selectinload(self.m.Order.orders_items)).order_by(self.m.Order.id).limit(limit)
        if customer_id is not None:
            stmt = stmt.where(self.m.Order.customer_id == customer_id)
        return list(self.s.scalars(stmt))
