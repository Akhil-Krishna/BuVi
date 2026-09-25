import { execFileSync } from "node:child_process";
import { test, expect } from "@playwright/test";
import { clearMfaAndSessions, DEMO } from "./reset";
import { signIn } from "./sign-in";

/**
 * Phase B5 DoD, in a real browser: a developer defines/edits a metric, moves
 * it draft -> approved -> deprecated, and the next chat run uses an approved
 * metric (Section 12: `_resolve_semantics` only ever sees `approved`
 * metrics); a client-role session never reaches the page. No step-up is
 * involved anywhere here -- `semantic:manage`'s create/approve/deprecate
 * endpoints carry no step-up requirement (Section 9's x-auth strings).
 */
test.describe.configure({ mode: "serial" });

const METRIC_NAME = `Order Count ${Date.now()}`;
const METRIC_SYNONYM = "ordercount";

/**
 * Every metric this file creates uses a fresh `Date.now()` suffix so the
 * `UNIQUE (tenant_id, name)` constraint never collides across runs -- but an
 * *approved* metric from a previous run is not otherwise revoked, and the
 * scripted provider's lexical matcher resolves every approved metric whose
 * name or synonym appears in the request (scripted_provider.py's
 * `_semantic`), capped at 5. A second run leaves two same-synonym approved
 * metrics both matching "ordercount", and query-gateway then rejects the
 * resulting ambiguous plan (`QUERY_REJECTED`) -- found the hard way, running
 * this suite twice in a row. Deprecating them doesn't fully clean the
 * catalog either (a deprecated row still exists), so this suite deletes its
 * own prior rows outright before each run, the same idempotency discipline
 * `e2e/reset.ts` applies to MFA/session/API-key state.
 */
function resetSemanticFixtures(): void {
  const sql = `
    DELETE FROM semantic.metrics WHERE name LIKE 'Order Count %' OR name LIKE 'Order Volume %';
    DELETE FROM semantic.dimensions WHERE name LIKE 'Order Status %';
  `;
  execFileSync(
    "docker",
    [
      "exec",
      "-i",
      process.env.PGCONTAINER ?? "buvi-dev-postgres-1",
      "psql",
      "-U",
      "postgres",
      "-d",
      "agentic_bi",
      "-q",
      "-v",
      "ON_ERROR_STOP=1",
    ],
    { input: sql, encoding: "utf-8" }
  );
}

test.beforeAll(() => {
  clearMfaAndSessions(DEMO.developer);
  resetSemanticFixtures();
});

function latestValidatedSql(): string {
  const output = execFileSync(
    "docker",
    [
      "exec",
      "-i",
      process.env.PGCONTAINER ?? "buvi-dev-postgres-1",
      "psql",
      "-U",
      "postgres",
      "-d",
      "agentic_bi",
      "-t",
      "-A",
      "-c",
      "SELECT validated_sql FROM dashboard.artifacts ORDER BY created_at DESC LIMIT 1",
    ],
    { encoding: "utf-8" }
  );
  return output.trim();
}

test("a developer defines a metric and moves it draft -> approved -> deprecated", async ({
  page,
}) => {
  await signIn(page, "demo-developer");
  await page.goto("/semantic");
  await expect(page.getByRole("heading", { name: "Business Metrics Catalog" })).toBeVisible();

  await page.getByRole("button", { name: "Define metric" }).click();
  await page.getByLabel("Metric display name").fill(METRIC_NAME);
  await page
    .getByLabel("Business definition")
    .fill("Number of orders placed, for the DoD's approved-metric proof.");
  await page.getByLabel("Source table").selectOption({ label: "sample-sales-db: sales.orders" });
  await page.getByLabel("Aggregation").selectOption("count");
  // The column select repopulates asynchronously from the table's real
  // catalog (B3's `getTable`, reused here), briefly disabled with the
  // *previous* table's options while the new fetch is in flight -- wait for
  // the actual expected option's text (React sets `<option>`'s `value` as a
  // DOM property, not a reflected HTML attribute, so a `[value=]` CSS
  // selector never matches it) rather than the disabled state, which can be
  // true of a stale, not-yet-reset select too.
  await expect(page.getByLabel("Column").getByText("id (integer)", { exact: true })).toHaveCount(1);
  await page.getByLabel("Column").selectOption({ label: "id (integer)" });
  await expect(page.getByTestId("expression-preview")).toHaveText("COUNT(id)");

  await page.locator('input[placeholder="revenue, income, top-line, ..."]').fill(METRIC_SYNONYM);
  await page.keyboard.press("Enter");

  await page.getByRole("button", { name: "Save as draft" }).click();

  const row = page.locator("tr", { hasText: METRIC_NAME });
  await expect(row).toBeVisible();
  await expect(row.getByText("draft", { exact: true })).toBeVisible({ timeout: 10_000 });

  await row.getByRole("button", { name: "Approve" }).click();
  await expect(row.getByText("approved", { exact: true })).toBeVisible({ timeout: 10_000 });

  await row.getByRole("button", { name: "Deprecate" }).click();
  await expect(row.getByText("deprecated", { exact: true })).toBeVisible({ timeout: 10_000 });
  // draft -> approved -> deprecated only (metric_expression.py's
  // `next_status`) -- a deprecated metric has no further action.
  await expect(row.getByRole("button")).toHaveCount(0);
});

test("a developer defines a dimension", async ({ page }) => {
  await signIn(page, "demo-developer");
  await page.goto("/semantic");
  await page.getByRole("button", { name: "Dimensions" }).click();

  await page.getByRole("button", { name: "Define dimension" }).click();
  const dimensionName = `Order Status ${Date.now()}`;
  await page.getByLabel("Name").fill(dimensionName);
  await page.getByLabel("Source table").selectOption({ label: "sample-sales-db: sales.orders" });
  await expect(page.getByLabel("Column").getByText("status (text)", { exact: true })).toHaveCount(
    1
  );
  await page.getByLabel("Column").selectOption({ label: "status (text)" });
  await page.getByRole("button", { name: "Save dimension" }).click();

  const dimensionRow = page.locator("tr", { hasText: dimensionName });
  await expect(dimensionRow).toBeVisible();
  await expect(dimensionRow.getByText("sales.orders.status")).toBeVisible();
});

test("an approved metric is used by the next chat run", async ({ page }) => {
  await signIn(page, "demo-developer");
  await page.goto("/semantic");

  const metricName = `Order Volume ${Date.now()}`;
  await page.getByRole("button", { name: "Define metric" }).click();
  await page.getByLabel("Metric display name").fill(metricName);
  await page.getByLabel("Source table").selectOption({ label: "sample-sales-db: sales.orders" });
  await page.getByLabel("Aggregation").selectOption("count");
  await expect(page.getByLabel("Column").getByText("id (integer)", { exact: true })).toHaveCount(1);
  await page.getByLabel("Column").selectOption({ label: "id (integer)" });
  await page.locator('input[placeholder="revenue, income, top-line, ..."]').fill(METRIC_SYNONYM);
  await page.keyboard.press("Enter");
  await page.getByRole("button", { name: "Save as draft" }).click();

  const row = page.locator("tr", { hasText: metricName });
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "Approve" }).click();
  await expect(row.getByText("approved", { exact: true })).toBeVisible({ timeout: 10_000 });

  await page.goto("/chat");
  await page
    .getByPlaceholder("Ask a follow-up question or specify a slice...")
    .fill(`Show orders trend by ${METRIC_SYNONYM}`);
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByRole("button", { name: "Pin to Dashboard" })).toBeVisible({
    timeout: 60_000,
  });

  // The proof that the *approved metric* was used, not the scripted
  // provider's generic fallback (which always aliases its measure "revenue"
  // and always aggregates with SUM, scripted_provider.py's `_plan`): the
  // metric's own alias is a slug of its display name (SemanticMetric.alias,
  // run_state.py), and COUNT(...) can only come from a metric resolution,
  // since the fallback is hardcoded to SUM.
  const alias = metricName
    .toLowerCase()
    .match(/[a-z0-9]+/g)!
    .join("_")
    .slice(0, 60);
  const sql = latestValidatedSql().toLowerCase();
  expect(sql).toContain("count(");
  // query-gateway's validated SQL quotes identifiers (`as "order_volume_..."`).
  expect(sql).toMatch(new RegExp(`as "?${alias}"?`));
});

test("a client-role user never reaches the semantic catalog", async ({ page }) => {
  await signIn(page, "demo-client");
  await expect(page.getByRole("link", { name: "Semantic" })).toHaveCount(0);

  await page.goto("/semantic");
  await expect(
    page.getByText("Your role does not include access to semantic management.")
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Define metric" })).toHaveCount(0);
});
