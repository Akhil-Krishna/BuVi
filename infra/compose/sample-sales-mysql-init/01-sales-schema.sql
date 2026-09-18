-- MySQL twin of sample-sales-db (Phase A8): the same `sales` data in a customer's MySQL 8.
--
-- In MySQL a schema is a database, so the data source's `allowed_schemas` is ["sales"].
-- Same rows as infra/compose/sample-sales-init (same generator formulas), so the vertical slice
-- gives the same answers on both engines. Idempotent: re-applied by
-- scripts/seed-sample-sales-mysql.sh, and mounted as an init script for fresh containers.
--
-- `buvi_reader` is SELECT-only on `sales` (Sections 13, 24). Throwaway development password.

CREATE DATABASE IF NOT EXISTS sales CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE sales;
SET SESSION cte_max_recursion_depth = 5000;

CREATE TABLE IF NOT EXISTS regions (
    id INT PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE
) COMMENT = 'Sales regions';

CREATE TABLE IF NOT EXISTS customers (
    id INT PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    email VARCHAR(200) NOT NULL COMMENT 'Contact email (personal data)',
    region_id INT NOT NULL,
    created_on DATE NOT NULL,
    CONSTRAINT customers_region_fk FOREIGN KEY (region_id) REFERENCES regions (id)
) COMMENT = 'Customer accounts';

CREATE TABLE IF NOT EXISTS products (
    id INT PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    category VARCHAR(50) NOT NULL,
    unit_price DECIMAL(10, 2) NOT NULL
) COMMENT = 'Product catalog';

CREATE TABLE IF NOT EXISTS orders (
    id INT PRIMARY KEY,
    customer_id INT NOT NULL,
    order_date DATE NOT NULL,
    status VARCHAR(20) NOT NULL,
    amount DECIMAL(12, 2) NOT NULL DEFAULT 0 COMMENT 'Order total in USD (sum of its items)',
    CONSTRAINT orders_customer_fk FOREIGN KEY (customer_id) REFERENCES customers (id),
    CONSTRAINT orders_status_ck CHECK (status IN ('completed', 'pending', 'refunded'))
) COMMENT = 'One row per order';

CREATE TABLE IF NOT EXISTS order_items (
    order_id INT NOT NULL,
    line_no INT NOT NULL,
    product_id INT NOT NULL,
    quantity INT NOT NULL,
    unit_price DECIMAL(10, 2) NOT NULL,
    PRIMARY KEY (order_id, line_no),
    CONSTRAINT order_items_order_fk FOREIGN KEY (order_id) REFERENCES orders (id),
    CONSTRAINT order_items_product_fk FOREIGN KEY (product_id) REFERENCES products (id),
    CONSTRAINT order_items_quantity_ck CHECK (quantity > 0)
) COMMENT = 'Order lines';

INSERT IGNORE INTO regions (id, name) VALUES
    (1, 'North America'), (2, 'Europe'), (3, 'Asia Pacific'), (4, 'Latin America');

INSERT IGNORE INTO products (id, name, category, unit_price)
WITH RECURSIVE g (n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM g WHERE n < 12)
SELECT n, CONCAT('Product ', n), ELT(1 + n % 3, 'Hardware', 'Software', 'Services'), 10 + n * 7.5
FROM g;

INSERT IGNORE INTO customers (id, name, email, region_id, created_on)
WITH RECURSIVE g (n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM g WHERE n < 200)
SELECT n, CONCAT('Customer ', n), CONCAT('customer', n, '@example.com'), 1 + n % 4,
       DATE_ADD(DATE '2025-01-01', INTERVAL n DAY)
FROM g;

INSERT IGNORE INTO orders (id, customer_id, order_date, status)
WITH RECURSIVE g (n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM g WHERE n < 2000)
SELECT n, 1 + (n * 7) % 200, DATE_ADD(DATE '2026-01-01', INTERVAL (n % 181) DAY),
       ELT(1 + n % 5, 'completed', 'completed', 'completed', 'refunded', 'pending')
FROM g;

INSERT IGNORE INTO order_items (order_id, line_no, product_id, quantity, unit_price)
WITH RECURSIVE item_lines (line) AS (SELECT 1 UNION ALL SELECT line + 1 FROM item_lines WHERE line < 3)
SELECT o.id, l.line, p.id, 1 + (o.id * l.line) % 5, p.unit_price
FROM orders o
JOIN item_lines l ON l.line <= 1 + o.id % 3
JOIN products p ON p.id = 1 + (o.id + l.line) % 12;

UPDATE orders o
JOIN (SELECT order_id, SUM(quantity * unit_price) AS total FROM order_items GROUP BY order_id) t
  ON t.order_id = o.id
SET o.amount = t.total
WHERE o.amount <> t.total;

ANALYZE TABLE regions, customers, products, orders, order_items;

CREATE USER IF NOT EXISTS 'buvi_reader'@'%' IDENTIFIED BY 'dev-reader-password';
GRANT SELECT ON sales.* TO 'buvi_reader'@'%';
