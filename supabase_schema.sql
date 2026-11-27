-- =============================================================================
-- MAZI IDIKA — Supabase / PostgreSQL Schema
-- =============================================================================
-- Instructions
-- 1. Open your Supabase project → SQL Editor → New query
-- 2. Paste this entire file and click Run
-- 3. All tables, enums, indexes, RLS policies, and triggers are created
--    in the correct dependency order
-- 4. Run once on a fresh database; re-running is safe (IF NOT EXISTS throughout)
-- =============================================================================


-- =============================================================================
-- SECTION 1: EXTENSIONS
-- =============================================================================

-- UUID generation (used for all primary keys)
CREATE EXTENSION IF NOT EXISTS "pgcrypto";


-- =============================================================================
-- SECTION 2: ENUM TYPES
-- =============================================================================

DO $$ BEGIN
  CREATE TYPE plan_tier AS ENUM ('free', 'starter', 'growth', 'business');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE payment_method AS ENUM ('cash', 'transfer', 'pos', 'credit');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE transaction_status AS ENUM ('completed', 'voided', 'pending');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE debt_status AS ENUM ('pending', 'partial', 'settled');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;


-- =============================================================================
-- SECTION 3: TABLES
-- (Ordered by dependency — no forward references)
-- =============================================================================

-- ── businesses ───────────────────────────────────────────────────────────────
-- Top-level tenant. Every piece of data belongs to exactly one business.
-- owner_id references auth.users (Supabase Auth).

CREATE TABLE IF NOT EXISTS businesses (
    id            TEXT        PRIMARY KEY DEFAULT gen_random_uuid()::text,
    name          TEXT        NOT NULL,
    phone         TEXT        NOT NULL UNIQUE,     -- normalised: 234xxxxxxxxxx
    address       TEXT,
    business_type TEXT,                             -- provision_store | pharmacy | etc.
    plan          plan_tier   NOT NULL DEFAULT 'free',
    owner_id      TEXT        NOT NULL,             -- Supabase auth.users UUID
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE  businesses            IS 'Top-level tenant. Every resource is scoped to one business.';
COMMENT ON COLUMN businesses.owner_id   IS 'Supabase auth.users UUID of the account owner.';
COMMENT ON COLUMN businesses.phone      IS 'Normalised phone number used to identify the sender from WhatsApp (e.g. 2348012345678).';


-- ── branches ─────────────────────────────────────────────────────────────────
-- A physical location belonging to a business.
-- A business always has at least one branch.

CREATE TABLE IF NOT EXISTS branches (
    id          TEXT        PRIMARY KEY DEFAULT gen_random_uuid()::text,
    business_id TEXT        NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    name        TEXT        NOT NULL,
    address     TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE branches IS 'A physical location (stall, shop, branch) belonging to a business.';


-- ── customers ────────────────────────────────────────────────────────────────
-- Named customers of a business. Used for debt tracking and purchase history.

CREATE TABLE IF NOT EXISTS customers (
    id          TEXT        PRIMARY KEY DEFAULT gen_random_uuid()::text,
    business_id TEXT        NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    name        TEXT        NOT NULL,
    phone       TEXT,                               -- optional; needed for auto-reminders
    notes       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON COLUMN customers.phone IS 'Customer phone number. Required for automatic WhatsApp debt reminders.';


-- ── products ─────────────────────────────────────────────────────────────────
-- Inventory items. Soft-deleted (is_deleted = true) rather than hard-deleted
-- so transaction history retains the product name.

CREATE TABLE IF NOT EXISTS products (
    id                  TEXT        PRIMARY KEY DEFAULT gen_random_uuid()::text,
    branch_id           TEXT        NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    name                TEXT        NOT NULL,
    category            TEXT,
    unit                TEXT        NOT NULL DEFAULT 'unit',  -- carton | bag | tin | piece | unit
    cost_price          NUMERIC(12,2),
    sell_price          NUMERIC(12,2),
    quantity            INTEGER     NOT NULL DEFAULT 0,
    low_stock_threshold INTEGER     NOT NULL DEFAULT 5,
    is_deleted          BOOLEAN     NOT NULL DEFAULT false,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT products_quantity_non_negative CHECK (quantity >= 0)
);

COMMENT ON TABLE  products               IS 'Inventory items for a branch. Soft-deleted to preserve sales history.';
COMMENT ON COLUMN products.unit          IS 'carton | bag | tin | piece | unit';
COMMENT ON COLUMN products.is_deleted    IS 'Soft delete flag. Never hard-delete products.';


-- ── transactions ─────────────────────────────────────────────────────────────
-- Sales transactions.
-- PRIMARY KEY is the CLIENT-generated tx_id (UUID).
-- INSERT ... ON CONFLICT (tx_id) DO NOTHING ensures offline sync idempotency.

CREATE TABLE IF NOT EXISTS transactions (
    tx_id          TEXT               PRIMARY KEY,   -- client-generated UUID
    branch_id      TEXT               NOT NULL REFERENCES branches(id),
    customer_id    TEXT               REFERENCES customers(id),
    customer_name  TEXT,                              -- walk-in snapshot (no customer record)
    payment_method payment_method     NOT NULL,
    status         transaction_status NOT NULL DEFAULT 'completed',
    total_amount   NUMERIC(14,2)      NOT NULL,
    notes          TEXT,
    recorded_at    TIMESTAMPTZ        NOT NULL DEFAULT now(),  -- may differ from created_at (offline)
    created_at     TIMESTAMPTZ        NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ        NOT NULL DEFAULT now()
);

COMMENT ON TABLE  transactions         IS 'Sales transactions. tx_id is client-generated for offline idempotency.';
COMMENT ON COLUMN transactions.tx_id   IS 'Client-generated UUID idempotency key. Use INSERT ... ON CONFLICT (tx_id) DO NOTHING.';
COMMENT ON COLUMN transactions.recorded_at IS 'Timestamp set by the client — may predate created_at for offline sales.';


-- ── transaction_items ────────────────────────────────────────────────────────
-- Line items within a transaction.
-- product_name is a snapshot so reports survive product deletion.

CREATE TABLE IF NOT EXISTS transaction_items (
    id                TEXT         PRIMARY KEY DEFAULT gen_random_uuid()::text,
    transaction_tx_id TEXT         NOT NULL REFERENCES transactions(tx_id) ON DELETE CASCADE,
    product_id        TEXT         NOT NULL REFERENCES products(id),
    product_name      TEXT         NOT NULL,  -- snapshot at time of sale
    quantity          INTEGER      NOT NULL,
    unit_price        NUMERIC(12,2) NOT NULL,
    total             NUMERIC(14,2) NOT NULL,  -- unit_price * quantity
    CONSTRAINT transaction_items_quantity_positive CHECK (quantity > 0)
);

COMMENT ON COLUMN transaction_items.product_name IS 'Snapshot of product name at time of sale. Preserved even if product is later renamed or deleted.';
COMMENT ON COLUMN transaction_items.total        IS 'unit_price × quantity. Stored as snapshot — not recomputed from live prices.';


-- ── debts ────────────────────────────────────────────────────────────────────
-- Credit sales and manually recorded debts.

CREATE TABLE IF NOT EXISTS debts (
    id              TEXT        PRIMARY KEY DEFAULT gen_random_uuid()::text,
    branch_id       TEXT        NOT NULL REFERENCES branches(id),
    customer_id     TEXT        REFERENCES customers(id),
    customer_name   TEXT,                             -- denormalised for walk-in debtors
    customer_phone  TEXT,                             -- captured at recording time for reminders
    sale_tx_id      TEXT        REFERENCES transactions(tx_id),
    original_amount NUMERIC(14,2) NOT NULL,
    amount_paid     NUMERIC(14,2) NOT NULL DEFAULT 0,
    description     TEXT,
    status          debt_status   NOT NULL DEFAULT 'pending',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT debts_amount_paid_non_negative CHECK (amount_paid >= 0),
    CONSTRAINT debts_amount_paid_le_original  CHECK (amount_paid <= original_amount)
);

COMMENT ON COLUMN debts.customer_phone IS 'Phone captured at debt-creation time, used for WhatsApp reminders even without a customer record.';
COMMENT ON COLUMN debts.sale_tx_id     IS 'Links to the originating transaction when debt was created from a credit sale.';


-- ── audit_logs ───────────────────────────────────────────────────────────────
-- Immutable append-only audit trail.
-- NEVER update or delete rows. Required on Business plan tier.

CREATE TABLE IF NOT EXISTS audit_logs (
    id           TEXT        PRIMARY KEY DEFAULT gen_random_uuid()::text,
    business_id  TEXT        NOT NULL REFERENCES businesses(id),
    branch_id    TEXT        REFERENCES branches(id),
    user_id      TEXT        NOT NULL,              -- Supabase auth.users UUID
    action       TEXT        NOT NULL,              -- e.g. sale.created, product.updated
    resource     TEXT        NOT NULL,              -- table name: transactions | products | debts
    resource_id  TEXT        NOT NULL,              -- PK of the affected row
    before_state JSONB,                             -- row state before mutation (NULL for inserts)
    after_state  JSONB,                             -- row state after mutation  (NULL for deletes)
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    -- No updated_at — audit rows are immutable
);

COMMENT ON TABLE audit_logs IS 'Immutable audit trail. Never update or delete rows.';


-- =============================================================================
-- SECTION 4: INDEXES
-- (All foreign keys + all columns used in WHERE / ORDER BY in the agent)
-- =============================================================================

-- businesses
CREATE INDEX IF NOT EXISTS idx_businesses_phone    ON businesses(phone);
CREATE INDEX IF NOT EXISTS idx_businesses_owner_id ON businesses(owner_id);

-- branches
CREATE INDEX IF NOT EXISTS idx_branches_business_id ON branches(business_id);

-- customers
CREATE INDEX IF NOT EXISTS idx_customers_business_id ON customers(business_id);
CREATE INDEX IF NOT EXISTS idx_customers_phone       ON customers(phone);

-- products
CREATE INDEX IF NOT EXISTS idx_products_branch_id   ON products(branch_id);
CREATE INDEX IF NOT EXISTS idx_products_name        ON products(branch_id, lower(name));  -- case-insensitive search
CREATE INDEX IF NOT EXISTS idx_products_low_stock   ON products(branch_id, quantity) WHERE is_deleted = false;
CREATE INDEX IF NOT EXISTS idx_products_not_deleted ON products(branch_id) WHERE is_deleted = false;

-- transactions
CREATE INDEX IF NOT EXISTS idx_transactions_branch_id   ON transactions(branch_id);
CREATE INDEX IF NOT EXISTS idx_transactions_recorded_at ON transactions(branch_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_transactions_status      ON transactions(branch_id, status);
CREATE INDEX IF NOT EXISTS idx_transactions_customer_id ON transactions(customer_id);

-- transaction_items
CREATE INDEX IF NOT EXISTS idx_tx_items_transaction ON transaction_items(transaction_tx_id);
CREATE INDEX IF NOT EXISTS idx_tx_items_product     ON transaction_items(product_id);

-- debts
CREATE INDEX IF NOT EXISTS idx_debts_branch_id      ON debts(branch_id);
CREATE INDEX IF NOT EXISTS idx_debts_status         ON debts(branch_id, status);
CREATE INDEX IF NOT EXISTS idx_debts_customer_name  ON debts(branch_id, lower(customer_name));
CREATE INDEX IF NOT EXISTS idx_debts_customer_id    ON debts(customer_id);
CREATE INDEX IF NOT EXISTS idx_debts_sale_tx_id     ON debts(sale_tx_id);

-- audit_logs
CREATE INDEX IF NOT EXISTS idx_audit_logs_business_id ON audit_logs(business_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_resource    ON audit_logs(business_id, resource, resource_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at  ON audit_logs(business_id, created_at);


-- =============================================================================
-- SECTION 5: updated_at AUTO-UPDATE TRIGGER
-- =============================================================================

CREATE OR REPLACE FUNCTION mazi_set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

-- Apply to every table that has updated_at

DO $$ 
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'businesses', 'branches', 'customers',
    'products', 'transactions', 'debts'
  ] LOOP
    EXECUTE format(
      'DROP TRIGGER IF EXISTS trg_set_updated_at ON %I;
       CREATE TRIGGER trg_set_updated_at
       BEFORE UPDATE ON %I
       FOR EACH ROW EXECUTE FUNCTION mazi_set_updated_at();',
      t, t
    );
  END LOOP;
END $$;


-- =============================================================================
-- SECTION 6: ROW-LEVEL SECURITY (RLS)
-- =============================================================================
-- RLS enforces multi-tenancy at the database layer.
-- Even if application code has a bug, one tenant cannot read another's data.
--
-- Policy design:
--   - Service-role key (used by FastAPI)   → bypasses RLS  (Supabase default)
--   - Anon / authenticated key (dashboard) → enforced by policies below
--
-- For the FastAPI backend running with the service-role key,
-- RLS is a safety net — the application MUST still filter by business_id.
-- =============================================================================

ALTER TABLE businesses       ENABLE ROW LEVEL SECURITY;
ALTER TABLE branches         ENABLE ROW LEVEL SECURITY;
ALTER TABLE customers        ENABLE ROW LEVEL SECURITY;
ALTER TABLE products         ENABLE ROW LEVEL SECURITY;
ALTER TABLE transactions     ENABLE ROW LEVEL SECURITY;
ALTER TABLE transaction_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE debts            ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs       ENABLE ROW LEVEL SECURITY;

-- ── businesses ────────────────────────────────────────────────────────────────
-- Owner can read and update their own business.
DROP POLICY IF EXISTS biz_owner_select ON businesses;
CREATE POLICY biz_owner_select ON businesses
    FOR SELECT USING (owner_id = auth.uid()::text);

DROP POLICY IF EXISTS biz_owner_update ON businesses;
CREATE POLICY biz_owner_update ON businesses
    FOR UPDATE USING (owner_id = auth.uid()::text);

DROP POLICY IF EXISTS biz_owner_insert ON businesses;
CREATE POLICY biz_owner_insert ON businesses
    FOR INSERT WITH CHECK (owner_id = auth.uid()::text);

-- ── branches ──────────────────────────────────────────────────────────────────
-- Users can only see branches belonging to their own businesses.
DROP POLICY IF EXISTS branches_owner ON branches;
CREATE POLICY branches_owner ON branches
    FOR ALL USING (
        business_id IN (
            SELECT id FROM businesses WHERE owner_id = auth.uid()::text
        )
    );

-- ── customers ─────────────────────────────────────────────────────────────────
DROP POLICY IF EXISTS customers_owner ON customers;
CREATE POLICY customers_owner ON customers
    FOR ALL USING (
        business_id IN (
            SELECT id FROM businesses WHERE owner_id = auth.uid()::text
        )
    );

-- ── products ──────────────────────────────────────────────────────────────────
DROP POLICY IF EXISTS products_branch_owner ON products;
CREATE POLICY products_branch_owner ON products
    FOR ALL USING (
        branch_id IN (
            SELECT br.id FROM branches br
            JOIN businesses biz ON biz.id = br.business_id
            WHERE biz.owner_id = auth.uid()::text
        )
    );

-- ── transactions ──────────────────────────────────────────────────────────────
DROP POLICY IF EXISTS transactions_branch_owner ON transactions;
CREATE POLICY transactions_branch_owner ON transactions
    FOR ALL USING (
        branch_id IN (
            SELECT br.id FROM branches br
            JOIN businesses biz ON biz.id = br.business_id
            WHERE biz.owner_id = auth.uid()::text
        )
    );

-- ── transaction_items ─────────────────────────────────────────────────────────
DROP POLICY IF EXISTS tx_items_via_transaction ON transaction_items;
CREATE POLICY tx_items_via_transaction ON transaction_items
    FOR ALL USING (
        transaction_tx_id IN (
            SELECT tx.tx_id FROM transactions tx
            JOIN branches br ON br.id = tx.branch_id
            JOIN businesses biz ON biz.id = br.business_id
            WHERE biz.owner_id = auth.uid()::text
        )
    );

-- ── debts ─────────────────────────────────────────────────────────────────────
DROP POLICY IF EXISTS debts_branch_owner ON debts;
CREATE POLICY debts_branch_owner ON debts
    FOR ALL USING (
        branch_id IN (
            SELECT br.id FROM branches br
            JOIN businesses biz ON biz.id = br.business_id
            WHERE biz.owner_id = auth.uid()::text
        )
    );

-- ── audit_logs ────────────────────────────────────────────────────────────────
-- Read-only for the business owner. Only the service role can insert.
DROP POLICY IF EXISTS audit_logs_owner_select ON audit_logs;
CREATE POLICY audit_logs_owner_select ON audit_logs
    FOR SELECT USING (
        business_id IN (
            SELECT id FROM businesses WHERE owner_id = auth.uid()::text
        )
    );


-- =============================================================================
-- SECTION 7: HELPER VIEWS
-- =============================================================================

-- ── v_outstanding_debts ───────────────────────────────────────────────────────
-- Pre-computed remaining balance per debt. Used by the WhatsApp agent
-- and the dashboard.

CREATE OR REPLACE VIEW v_outstanding_debts AS
SELECT
    d.id,
    d.branch_id,
    d.customer_id,
    d.customer_name,
    d.customer_phone,
    d.sale_tx_id,
    d.original_amount,
    d.amount_paid,
    (d.original_amount - d.amount_paid) AS amount_remaining,
    d.description,
    d.status,
    d.created_at,
    d.updated_at
FROM debts d
WHERE d.status != 'settled';

COMMENT ON VIEW v_outstanding_debts IS 'All non-settled debts with pre-computed remaining balance.';


-- ── v_low_stock_products ─────────────────────────────────────────────────────
-- Products at or below their low_stock_threshold, excluding deleted items.

CREATE OR REPLACE VIEW v_low_stock_products AS
SELECT
    p.id,
    p.branch_id,
    p.name,
    p.category,
    p.unit,
    p.quantity,
    p.low_stock_threshold,
    p.sell_price,
    p.cost_price,
    (p.quantity = 0) AS is_out_of_stock
FROM products p
WHERE p.is_deleted = false
  AND p.quantity <= p.low_stock_threshold;

COMMENT ON VIEW v_low_stock_products IS 'Products at or below low_stock_threshold. Excludes soft-deleted products.';


-- ── v_branch_daily_summary ───────────────────────────────────────────────────
-- Revenue, transaction count, and profit per branch per calendar day (UTC).
-- Useful for the daily summary report.

CREATE OR REPLACE VIEW v_branch_daily_summary AS
SELECT
    tx.branch_id,
    DATE(tx.recorded_at AT TIME ZONE 'UTC')    AS sale_date,
    COUNT(tx.tx_id)                             AS transaction_count,
    SUM(tx.total_amount)                        AS total_revenue,
    SUM(
        CASE WHEN tx.payment_method = 'credit' THEN 0 ELSE tx.total_amount END
    )                                           AS cash_revenue
FROM transactions tx
WHERE tx.status = 'completed'
GROUP BY tx.branch_id, DATE(tx.recorded_at AT TIME ZONE 'UTC');

COMMENT ON VIEW v_branch_daily_summary IS 'Daily revenue totals per branch. Used by the today/week/month summary handler.';


-- ── v_top_products ────────────────────────────────────────────────────────────
-- Product revenue ranking per branch across all time.

CREATE OR REPLACE VIEW v_top_products AS
SELECT
    tx.branch_id,
    ti.product_id,
    ti.product_name,
    SUM(ti.total)    AS total_revenue,
    SUM(ti.quantity) AS total_units_sold,
    COUNT(DISTINCT tx.tx_id) AS transaction_count
FROM transaction_items ti
JOIN transactions tx ON tx.tx_id = ti.transaction_tx_id
WHERE tx.status = 'completed'
GROUP BY tx.branch_id, ti.product_id, ti.product_name
ORDER BY total_revenue DESC;

COMMENT ON VIEW v_top_products IS 'Product revenue ranking per branch. Used by the top products report handler.';


-- =============================================================================
-- SECTION 8: SEED — DEFAULT BRANCH FUNCTION
-- =============================================================================
-- Called by the application after creating a new business to ensure
-- a default branch always exists.

CREATE OR REPLACE FUNCTION mazi_create_default_branch(
    p_business_id TEXT,
    p_branch_name TEXT DEFAULT 'Main Branch'
)
RETURNS TEXT   -- returns the new branch id
LANGUAGE plpgsql AS $$
DECLARE
    v_branch_id TEXT;
BEGIN
    -- Only create if no branch exists yet
    SELECT id INTO v_branch_id
    FROM branches
    WHERE business_id = p_business_id
    LIMIT 1;

    IF v_branch_id IS NULL THEN
        v_branch_id := gen_random_uuid()::text;
        INSERT INTO branches (id, business_id, name)
        VALUES (v_branch_id, p_business_id, p_branch_name);
    END IF;

    RETURN v_branch_id;
END;
$$;

COMMENT ON FUNCTION mazi_create_default_branch IS
    'Ensures a business always has at least one branch. Idempotent — safe to call multiple times.';


-- =============================================================================
-- SECTION 9: IDEMPOTENT SALE INSERT FUNCTION
-- =============================================================================
-- Use this to insert a transaction safely from offline sync.
-- ON CONFLICT (tx_id) DO NOTHING prevents duplicate sales.

CREATE OR REPLACE FUNCTION mazi_record_sale(
    p_tx_id          TEXT,
    p_branch_id      TEXT,
    p_customer_id    TEXT,
    p_customer_name  TEXT,
    p_payment_method payment_method,
    p_total_amount   NUMERIC,
    p_notes          TEXT,
    p_recorded_at    TIMESTAMPTZ
)
RETURNS TEXT   -- returns 'inserted' or 'duplicate'
LANGUAGE plpgsql AS $$
DECLARE
    v_existed BOOLEAN;
BEGIN
    SELECT EXISTS(SELECT 1 FROM transactions WHERE tx_id = p_tx_id) INTO v_existed;

    IF v_existed THEN
        RETURN 'duplicate';
    END IF;

    INSERT INTO transactions (
        tx_id, branch_id, customer_id, customer_name,
        payment_method, status, total_amount, notes, recorded_at
    )
    VALUES (
        p_tx_id, p_branch_id, p_customer_id, p_customer_name,
        p_payment_method, 'completed', p_total_amount, p_notes, p_recorded_at
    )
    ON CONFLICT (tx_id) DO NOTHING;

    RETURN 'inserted';
END;
$$;

COMMENT ON FUNCTION mazi_record_sale IS
    'Idempotent sale insert. Returns ''inserted'' or ''duplicate''. Safe for offline sync replay.';


-- =============================================================================
-- SECTION 10: VERIFICATION QUERIES
-- Run these after executing the schema to confirm everything was created.
-- =============================================================================

/*
-- Check all tables exist:
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN (
    'businesses','branches','customers','products',
    'transactions','transaction_items','debts','audit_logs'
  )
ORDER BY table_name;

-- Check all enums exist:
SELECT typname FROM pg_type
WHERE typname IN ('plan_tier','payment_method','transaction_status','debt_status');

-- Check RLS is enabled:
SELECT tablename, rowsecurity
FROM pg_tables
WHERE schemaname = 'public'
  AND tablename IN (
    'businesses','branches','customers','products',
    'transactions','transaction_items','debts','audit_logs'
  );

-- Check indexes:
SELECT indexname, tablename
FROM pg_indexes
WHERE schemaname = 'public'
  AND indexname LIKE 'idx_%'
ORDER BY tablename, indexname;

-- Check views:
SELECT table_name FROM information_schema.views
WHERE table_schema = 'public';
*/
