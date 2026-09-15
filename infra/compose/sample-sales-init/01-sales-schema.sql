-- Sample customer database for the first vertical slice (Sections 27, 32).
--
-- `sample-sales-db` plays a *customer's* Postgres: the platform connects to it as a
-- data source (Phase A3) and later queries it (Phase A4). Idempotent, so
-- `scripts/seed-sample-sales.sh` can re-apply it to an existing container; also mounted
-- as an init script so a fresh container starts seeded.
--
-- `buvi_reader` is the least-privilege, read-only principal the platform is given
-- (Sections 13, 24): CONNECT, USAGE on `sales`, SELECT on its tables -- nothing else.
-- The password is a throwaway development value, like every credential in compose.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'buvi_reader') THEN
        CREATE ROLE buvi_reader LOGIN PASSWORD 'dev-reader-password';
    END IF;
END
$$;

CREATE SCHEMA IF NOT EXISTS sales;
COMMENT ON SCHEMA sales IS 'Orders, customers and products for the demo tenant';

CREATE TABLE IF NOT EXISTS sales.regions (
    id integer PRIMARY KEY,
    name text NOT NULL UNIQUE
);
COMMENT ON TABLE sales.regions IS 'Sales regions';

CREATE TABLE IF NOT EXISTS sales.customers (
    id integer PRIMARY KEY,
    name text NOT NULL,
    email text NOT NULL,
    region_id integer NOT NULL REFERENCES sales.regions (id),
    created_on date NOT NULL
);
COMMENT ON TABLE sales.customers IS 'Customer accounts';
COMMENT ON COLUMN sales.customers.email IS 'Contact email (personal data)';

CREATE TABLE IF NOT EXISTS sales.products (
    id integer PRIMARY KEY,
    name text NOT NULL,
    category text NOT NULL,
    unit_price numeric(10, 2) NOT NULL
);
COMMENT ON TABLE sales.products IS 'Product catalog';

CREATE TABLE IF NOT EXISTS sales.orders (
    id integer PRIMARY KEY,
    customer_id integer NOT NULL REFERENCES sales.customers (id),
    order_date date NOT NULL,
    status text NOT NULL CHECK (status IN ('completed', 'pending', 'refunded')),
    amount numeric(12, 2) NOT NULL DEFAULT 0
);
COMMENT ON TABLE sales.orders IS 'One row per order';
COMMENT ON COLUMN sales.orders.amount IS 'Order total in USD (sum of its items)';

CREATE TABLE IF NOT EXISTS sales.order_items (
    order_id integer NOT NULL REFERENCES sales.orders (id),
    line_no integer NOT NULL,
    product_id integer NOT NULL REFERENCES sales.products (id),
    quantity integer NOT NULL CHECK (quantity > 0),
    unit_price numeric(10, 2) NOT NULL,
    PRIMARY KEY (order_id, line_no)
);
COMMENT ON TABLE sales.order_items IS 'Order lines';

INSERT INTO sales.regions (id, name) VALUES
    (1, 'North America'), (2, 'Europe'), (3, 'Asia Pacific'), (4, 'Latin America')
ON CONFLICT (id) DO NOTHING;

INSERT INTO sales.products (id, name, category, unit_price)
SELECT g, 'Product ' || g, (ARRAY['Hardware', 'Software', 'Services'])[1 + g % 3], 10 + g * 7.5
FROM generate_series(1, 12) AS g
ON CONFLICT (id) DO NOTHING;

INSERT INTO sales.customers (id, name, email, region_id, created_on)
SELECT g, 'Customer ' || g, 'customer' || g || '@example.com', 1 + g % 4, DATE '2025-01-01' + g
FROM generate_series(1, 200) AS g
ON CONFLICT (id) DO NOTHING;

INSERT INTO sales.orders (id, customer_id, order_date, status)
SELECT g,
       1 + (g * 7) % 200,
       DATE '2026-01-01' + (g % 181),
       (ARRAY['completed', 'completed', 'completed', 'refunded', 'pending'])[1 + g % 5]
FROM generate_series(1, 2000) AS g
ON CONFLICT (id) DO NOTHING;

INSERT INTO sales.order_items (order_id, line_no, product_id, quantity, unit_price)
SELECT o.id, line, p.id, 1 + (o.id * line) % 5, p.unit_price
FROM sales.orders o
CROSS JOIN LATERAL generate_series(1, 1 + o.id % 3) AS line
JOIN sales.products p ON p.id = 1 + (o.id + line) % 12
ON CONFLICT (order_id, line_no) DO NOTHING;

UPDATE sales.orders o
SET amount = totals.total
FROM (
    SELECT order_id, sum(quantity * unit_price) AS total
    FROM sales.order_items GROUP BY order_id
) AS totals
WHERE totals.order_id = o.id AND o.amount IS DISTINCT FROM totals.total;

ANALYZE sales.regions, sales.customers, sales.products, sales.orders, sales.order_items;

GRANT CONNECT ON DATABASE sample_sales TO buvi_reader;
GRANT USAGE ON SCHEMA sales TO buvi_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA sales TO buvi_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA sales GRANT SELECT ON TABLES TO buvi_reader;
