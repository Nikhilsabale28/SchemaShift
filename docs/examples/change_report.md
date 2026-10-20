# MongoDB -> SQL change report (map revision 1, hash ef20235471cc)

## `customers` <- collection `customers` (ORM class `Customer`)

| Column | SQL type | Source path | Rule | Nullable | Confidence |
|---|---|---|---|---|---|
| id | VARCHAR(24) | _id | direct | no | 1.0 |
| address_city | TEXT | address.city | flatten | no | 1.0 |
| address_postal_code | TEXT | address.postalCode | flatten | no | 1.0 |
| address_state | TEXT | address.state | flatten | no | 1.0 |
| created_at | TIMESTAMPTZ | createdAt | direct | no | 1.0 |
| email | TEXT | email | direct | no | 1.0 |
| full_name | TEXT | fullName | direct | no | 1.0 |
| phone | TEXT | phone | direct | yes | 1.0 |
| profile_newsletter | BOOLEAN | profile.newsletter | flatten | yes | 1.0 |
| profile_source | TEXT | profile.source | flatten | yes | 1.0 |

## `products` <- collection `products` (ORM class `Product`)

| Column | SQL type | Source path | Rule | Nullable | Confidence |
|---|---|---|---|---|---|
| id | VARCHAR(24) | _id | direct | no | 1.0 |
| category | TEXT | category | direct | no | 1.0 |
| name | TEXT | name | direct | no | 1.0 |
| price | NUMERIC(12,2) | price | direct | no | 1.0 |
| stock_quantity | BIGINT | stockQuantity | direct | no | 1.0 |

## `orders` <- collection `orders` (ORM class `Order`)

| Column | SQL type | Source path | Rule | Nullable | Confidence |
|---|---|---|---|---|---|
| id | VARCHAR(24) | _id | direct | no | 1.0 |
| customer_id | VARCHAR(24) | customerId | direct | no | 1.0 |
| order_date | TIMESTAMPTZ | orderDate | direct | no | 1.0 |
| shipping_method | TEXT | shipping.method | flatten | yes | 1.0 |
| shipping_tracking_number | TEXT | shipping.trackingNumber | flatten | yes | 1.0 |
| status | TEXT | status | direct | no | 1.0 |

Foreign key `customer_id` -> `customers.id`

## `orders_items` <- array `items` of `orders` (ORM class `OrderItem`)

| Column | SQL type | Source path | Rule | Nullable | Confidence |
|---|---|---|---|---|---|
| id | BIGSERIAL | (generated) | generated | no | 1.0 |
| order_id | VARCHAR(24) | _id | parent_key | no | 1.0 |
| position | BIGINT | (generated) | array_index | no | 1.0 |
| product_id | VARCHAR(24) | items[].productId | flatten | no | 0.95 |
| quantity | BIGINT | items[].quantity | flatten | no | 0.95 |
| unit_price | NUMERIC(12,2) | items[].unitPrice | flatten | no | 0.95 |

Foreign key `order_id` -> `orders.id`

Foreign key `product_id` -> `products.id`

## Access-pattern changes

- `db.orders.find_one({...})` with embedded `items` becomes a join: `orders` -> `orders_items` (via `OrderItem.order_id`).
- Embedded `address` becomes flat `address_*` columns on `customers`.
- References by ObjectId become VARCHAR(24) foreign keys enforced by PostgreSQL.
