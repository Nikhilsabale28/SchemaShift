"""CRUD demo against PostgreSQL through the GENERATED ORM (MongoDB is not used here)."""
import sys, uuid
from decimal import Decimal
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sqlalchemy.orm import Session
from app.config import load_settings
from app.orm.loader import load_models
from app.orm.repository import Repository, OrderService, make_engine

def main():
    st = load_settings(); st.require_postgres_configured()
    m = load_models(st.generated_dir / "orm_models.py")
    engine = make_engine(st.postgres_url)
    with Session(engine) as s:
        customers = Repository(s, m.Customer)
        new_id = uuid.uuid4().hex[:24]
        print("CREATE:", customers.create(id=new_id, full_name="Demo Student", email="demo@example.com",
                                          phone=None, created_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
                                          address_city="Pune", address_state="MH", address_postal_code="411001").id)
        print("READ:  ", customers.get(new_id).full_name)
        customers.update(new_id, phone="+91-9000000000", address_city="Mumbai")
        c = customers.get(new_id); print("UPDATE:", c.phone, c.address_city)
        s.commit()
        print("DELETE:", customers.delete(new_id)); s.commit()
        print("\nOrders with line items (first 3):")
        for o in OrderService(s, m).orders_with_items(limit=3):
            print(f"  order {o.id} status={o.status} customer={o.customer.full_name}")
            for it in o.orders_items:
                print(f"     item #{it.position}: product={it.product_id} qty={it.quantity} unit_price={it.unit_price}")
        print("\nProducts:", len(Repository(s, m.Product).list()), "rows read through the ORM")

if __name__ == "__main__":
    main()
