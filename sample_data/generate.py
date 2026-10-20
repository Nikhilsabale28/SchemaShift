"""Deterministic demo dataset: 10 customers, 8 products, 20 orders. No external download.

generate_seed(id_factory, decimal_factory) -> {collection: [documents]}
With pymongo: ids are bson.ObjectId, prices bson Decimal128. Without: FakeObjectId / Decimal.
"""
from __future__ import annotations
import datetime as dt
from decimal import Decimal

class FakeObjectId:
    """Stand-in used only when bson is not installed (tests / --memory demo)."""
    def __init__(self, hexstr): self.hex = hexstr
    def __str__(self): return self.hex
    def __repr__(self): return f"FakeObjectId({self.hex!r})"
    def __eq__(self, o): return isinstance(o, FakeObjectId) and o.hex == self.hex
    def __hash__(self): return hash(self.hex)

def fake_id(prefix: int, n: int) -> FakeObjectId:
    return FakeObjectId(f"{prefix:04x}{n:020x}")

CITIES = [("Pune", "MH", "411001"), ("Mumbai", "MH", "400001"), ("Delhi", "DL", "110001"),
          ("Bengaluru", "KA", "560001"), ("Chennai", "TN", "600001"), ("Hyderabad", "TG", "500001"),
          ("Kolkata", "WB", "700001"), ("Ahmedabad", "GJ", "380001"), ("Jaipur", "RJ", "302001"),
          ("Kochi", "KL", "682001")]
NAMES = ["Asha Patil", "Ben Carter", "Chitra Rao", "Dev Malhotra", "Esha Nair", "Farhan Sheikh",
         "Gita Menon", "Harsh Vora", "Isha Kapoor", "Jay Banerjee"]
PRODUCTS = [("Wireless Mouse", "Accessories", "799.00", 120), ("Mechanical Keyboard", "Accessories", "3499.00", 45),
            ("USB-C Hub", "Accessories", "1899.50", 80), ("27in Monitor", "Displays", "15999.00", 18),
            ("Laptop Stand", "Furniture", "1299.00", 60), ("Webcam HD", "Video", "2499.00", 33),
            ("Noise-Cancelling Headset", "Audio", "7999.00", 25), ("Desk Lamp", "Furniture", "999.00", 70)]
STATUSES = ["pending", "paid", "shipped", "delivered", "cancelled"]

def generate_seed(id_factory=fake_id, decimal_factory=Decimal) -> dict:
    customers = []
    for i, name in enumerate(NAMES):
        city, state, postal = CITIES[i]
        doc = {"_id": id_factory(1, i + 1), "fullName": name,
               "email": name.lower().replace(" ", ".") + "@example.com",
               "phone": None if i % 3 == 0 else f"+91-98{i:02d}00{i:04d}",
               "createdAt": dt.datetime(2024, 1, 1 + i, 9, 30, 0),
               "address": {"city": city, "state": state, "postalCode": postal}}
        if i % 2 == 0:
            doc["profile"] = {"newsletter": i % 4 == 0, "source": "web" if i % 4 == 0 else "referral"}
        customers.append(doc)
    products = [{"_id": id_factory(2, i + 1), "name": n, "category": c,
                 "price": decimal_factory(price), "stockQuantity": stock}
                for i, (n, c, price, stock) in enumerate(PRODUCTS)]
    orders = []
    for i in range(20):
        n_items = 1 + (i % 3)
        items = []
        for j in range(n_items):
            p = (i * 3 + j * 2) % len(PRODUCTS)
            items.append({"productId": products[p]["_id"], "quantity": 1 + ((i + j) % 4),
                          "unitPrice": decimal_factory(PRODUCTS[p][2])})
        doc = {"_id": id_factory(3, i + 1), "customerId": customers[(i * 7) % len(customers)]["_id"],
               "orderDate": dt.datetime(2024, 3, 1 + i, 14, 0, 0), "status": STATUSES[i % len(STATUSES)],
               "items": items}
        if i % 3 != 2:
            doc["shipping"] = {"method": "express" if i % 2 else "standard",
                               "trackingNumber": f"TRK{i:06d}" if i % 5 else None}
        orders.append(doc)
    return {"customers": customers, "products": products, "orders": orders}

def seed_mongo(uri: str, database: str, timeout_ms: int = 5000) -> dict:
    """Reset ONLY the three demo collections in `database`, then insert the dataset."""
    from pymongo import MongoClient
    from bson import ObjectId
    from bson.decimal128 import Decimal128
    data = generate_seed(lambda p, n: ObjectId(f"{p:04x}{n:020x}"), lambda s: Decimal128(s))
    client = MongoClient(uri, serverSelectionTimeoutMS=timeout_ms)
    db = client[database]
    counts = {}
    for name, docs in data.items():
        db[name].delete_many({})
        db[name].insert_many(docs)
        counts[name] = db[name].count_documents({})
    return counts
