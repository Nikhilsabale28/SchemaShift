import datetime as dt

class ObjectId:
    def __init__(self, v): self.v = v
    def __str__(self): return self.v

def sample_collections():
    cust = [{"_id": ObjectId("c1"), "name": "Asha", "email": "a@x.io", "phone": None,
             "address": {"city": "Pune", "state": "MH", "postalCode": "411001"}},
            {"_id": ObjectId("c2"), "name": "Ben", "email": "b@x.io",
             "address": {"city": "Delhi", "state": "DL", "postalCode": "110001"}}]
    prod = [{"_id": ObjectId("p1"), "name": "Widget", "price": 9.5, "stock": 3}]
    orders = [{"_id": ObjectId("o1"), "customerId": ObjectId("c1"), "orderDate": dt.datetime(2024, 1, 1),
               "items": [{"productId": ObjectId("p1"), "quantity": 2, "unitPrice": 9.5}]}]
    return {"customers": cust, "products": prod, "orders": orders}
