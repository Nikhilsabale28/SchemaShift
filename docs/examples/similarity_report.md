# SchemaShift similarity report

**Overall match: 99.4%**

Weights: {'name': 0.5, 'type': 0.3, 'structure': 0.2}  |  review threshold: 0.75

Aggregation: table = mean(field scores incl. unmatched=0); overall = sum(all field scores)/count(all fields)

## Per table

| Table | Match % |
|---|---|
| customers | 100.0 |
| orders | 100.0 |
| orders_items | 96.25 |
| products | 100.0 |

Counts: {'matched': 25, 'guessed': 0, 'unmatched': 0, 'needs_review': 0}

## Fields

| Source path | Target | Name | Type | Struct | Score | Status | Review |
|---|---|---|---|---|---|---|---|
| address.city | customers.address_city | 1.0 | 1.0 | 1.0 | 1.0 | matched |  |
| address.postalCode | customers.address_postal_code | 1.0 | 1.0 | 1.0 | 1.0 | matched |  |
| address.state | customers.address_state | 1.0 | 1.0 | 1.0 | 1.0 | matched |  |
| createdAt | customers.created_at | 1.0 | 1.0 | 1.0 | 1.0 | matched |  |
| email | customers.email | 1.0 | 1.0 | 1.0 | 1.0 | matched |  |
| fullName | customers.full_name | 1.0 | 1.0 | 1.0 | 1.0 | matched |  |
| _id | customers.id | 1.0 | 1.0 | 1.0 | 1.0 | matched |  |
| phone | customers.phone | 1.0 | 1.0 | 1.0 | 1.0 | matched |  |
| profile.newsletter | customers.profile_newsletter | 1.0 | 1.0 | 1.0 | 1.0 | matched |  |
| profile.source | customers.profile_source | 1.0 | 1.0 | 1.0 | 1.0 | matched |  |
| customerId | orders.customer_id | 1.0 | 1.0 | 1.0 | 1.0 | matched |  |
| _id | orders.id | 1.0 | 1.0 | 1.0 | 1.0 | matched |  |
| orderDate | orders.order_date | 1.0 | 1.0 | 1.0 | 1.0 | matched |  |
| shipping.method | orders.shipping_method | 1.0 | 1.0 | 1.0 | 1.0 | matched |  |
| shipping.trackingNumber | orders.shipping_tracking_number | 1.0 | 1.0 | 1.0 | 1.0 | matched |  |
| status | orders.status | 1.0 | 1.0 | 1.0 | 1.0 | matched |  |
| items[] (parent _id) | orders_items.order_id | 1.0 | 1.0 | 1.0 | 1.0 | matched |  |
| items[].productId | orders_items.product_id | 0.9 | 1.0 | 1.0 | 0.95 | matched |  |
| items[].quantity | orders_items.quantity | 0.9 | 1.0 | 1.0 | 0.95 | matched |  |
| items[].unitPrice | orders_items.unit_price | 0.9 | 1.0 | 1.0 | 0.95 | matched |  |
| category | products.category | 1.0 | 1.0 | 1.0 | 1.0 | matched |  |
| _id | products.id | 1.0 | 1.0 | 1.0 | 1.0 | matched |  |
| name | products.name | 1.0 | 1.0 | 1.0 | 1.0 | matched |  |
| price | products.price | 1.0 | 1.0 | 1.0 | 1.0 | matched |  |
| stockQuantity | products.stock_quantity | 1.0 | 1.0 | 1.0 | 1.0 | matched |  |
