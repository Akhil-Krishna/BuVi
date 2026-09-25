import { createHmac } from "node:crypto";
import { test, expect, type Page } from "@playwright/test";
import { clearMfaAndSessions, DEMO } from "./reset";
import { signIn } from "./sign-in";

/**
 * Phase B4 DoD, in a real browser: a developer browses the catalog (B3's
 * browser, reused here), writes/executes SQL through a real CodeMirror 6
 * editor (line numbers, SQL syntax highlighting, not a plain textarea), sees
 * results, and sends a result to the chart flow; execution above the export
 * row threshold (10,000 rows -- query_service.py's `export_step_up_rows`)
 * prompts step-up; a client-role session never reaches the page. Runs
 * against the real `sample-sales-db` Postgres instance
 * `provision_demo_data_source.py` connects (shared with
 * `chat-and-dashboards.spec.ts`'s precondition).
 */
test.describe.configure({ mode: "serial" });

const SELECT_ALL = process.platform === "darwin" ? "Meta+A" : "Control+A";

/** CodeMirror is a contenteditable widget, not a `<textarea>` -- `.fill()`
 * bypasses its own document model, so this clicks in, selects everything
 * with the editor's own keymap binding, and types the replacement. */
async function fillSqlEditor(page: Page, text: string): Promise<void> {
  const content = page.locator(".cm-content");
  await content.click();
  await page.keyboard.press(SELECT_ALL);
  await page.keyboard.press("Backspace");
  await page.keyboard.type(text);
}

test.beforeAll(async ({ browser }) => {
  clearMfaAndSessions(DEMO.developer, DEMO.admin);

  // `sql:execute` on a specific connection needs a per-connection grant for
  // everyone but org_admin (Section 7.1) -- grant it through B3's real UI
  // rather than a backend shortcut, since that grant is itself a shipped
  // product feature, not test scaffolding. `clearMfaAndSessions` above does
  // not revoke it (out of scope -- it resets factors/sessions/api keys, not
  // per-connection grants), so a second suite run finds the developer
  // already granted; the picker then correctly omits them as not
  // `grantable`, which is the signal to skip rather than an error.
  const admin = await browser.newContext();
  const adminPage = await admin.newPage();
  await signIn(adminPage, "demo-admin");
  await adminPage.goto("/data");
  await adminPage.getByRole("button", { name: /sample-sales-db/ }).click();
  await expect(adminPage.getByText("sql:execute grants")).toBeVisible();
  // Grants load asynchronously on expand (client-fetched, not a server
  // prop) -- wait for that fetch to settle before reading the picker,
  // otherwise "not yet loaded" and "already granted" look identical.
  await expect(adminPage.getByText("Loading...")).toHaveCount(0);
  const alreadyGranted =
    (await adminPage.getByRole("cell", { name: "developer@demo.example.com" }).count()) > 0;
  if (!alreadyGranted) {
    await adminPage
      .getByRole("combobox")
      .selectOption({ label: "developer@demo.example.com (developer)" });
    await adminPage.getByRole("button", { name: "Grant sql:execute" }).click();
    await expect(
      adminPage.getByRole("cell", { name: "developer@demo.example.com" })
    ).toBeVisible();
  }
  await admin.close();
});

function totp(base32Secret: string, atMs = Date.now()): string {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const char of base32Secret.replace(/=+$/, "").toUpperCase()) {
    const index = alphabet.indexOf(char);
    if (index === -1) continue;
    bits += index.toString(2).padStart(5, "0");
  }
  const key = Buffer.from((bits.match(/.{8}/g) ?? []).map((byte) => parseInt(byte, 2)));
  const counter = Math.floor(atMs / 1000 / 30);
  const message = Buffer.alloc(8);
  message.writeUInt32BE(Math.floor(counter / 2 ** 32), 0);
  message.writeUInt32BE(counter >>> 0, 4);
  const digest = createHmac("sha1", key).update(message).digest();
  const offset = digest[digest.length - 1] & 0x0f;
  const binary =
    ((digest[offset] & 0x7f) << 24) |
    ((digest[offset + 1] & 0xff) << 16) |
    ((digest[offset + 2] & 0xff) << 8) |
    (digest[offset + 3] & 0xff);
  return (binary % 1_000_000).toString().padStart(6, "0");
}

test("the SQL editor is CodeMirror 6: line numbers and real syntax highlighting", async ({
  page,
}, testInfo) => {
  await signIn(page, "demo-developer");
  await page.goto("/sql");
  await expect(page.getByRole("heading", { name: "SQL Lab" })).toBeVisible();

  // A plain <textarea> has none of this structure -- these are CodeMirror 6's
  // own stable core class names (@codemirror/view's base theme), not
  // anything this app's theme invented.
  await expect(page.locator(".cm-editor")).toBeVisible();
  // CodeMirror also renders a hidden measurement gutter element (for sizing
  // the column before real content lays out) alongside the visible ones --
  // `:visible` filters to what an actual user sees.
  const gutter = page.locator(".cm-lineNumbers .cm-gutterElement:visible");
  await expect(gutter.first()).toBeVisible();

  await fillSqlEditor(page, "select id, name\nfrom sales.regions\norder by id");

  // Three lines typed -> three line-number cells (CodeMirror 6 renders one
  // gutter element per visible line, "1".."3" here).
  await expect(gutter).toHaveCount(3);
  await expect(gutter.nth(0)).toHaveText("1");
  await expect(gutter.nth(2)).toHaveText("3");

  // Real syntax highlighting, not a screenshot-only claim: the "select"
  // keyword token must actually be painted in this app's own keyword color
  // (SqlEditor.tsx's theme), distinct from plain identifier text -- proves
  // @codemirror/lang-sql's SQL grammar is tokenizing, not just the base
  // editor chrome rendering.
  const keywordColor = await page
    .locator(".cm-content .cm-line")
    .first()
    .locator("span", { hasText: "select" })
    .first()
    .evaluate((el) => getComputedStyle(el).color);
  const identifierColor = await page
    .locator(".cm-content .cm-line")
    .first()
    .locator("span", { hasText: "id" })
    .first()
    .evaluate((el) => getComputedStyle(el).color);
  expect(keywordColor).toBe("rgb(30, 79, 184)"); // #1e4fb8, this app's keyword color
  expect(keywordColor).not.toBe(identifierColor);

  await testInfo.attach("sql-editor-codemirror", {
    body: await page.locator(".cm-editor").screenshot(),
    contentType: "image/png",
  });
});

test("a developer validates, runs, and sends a result to the chart flow", async ({ page }) => {
  await signIn(page, "demo-developer");
  await page.goto("/sql");

  // The catalog browser (B3) is reused here, not rebuilt.
  await expect(page.getByText("sales.regions")).toBeVisible();
  await page.getByText("sales.regions").click();
  await expect(page.locator(".cm-content")).toContainText("sales.regions");

  await fillSqlEditor(page, "select id, name from sales.regions order by id");

  await page.getByRole("button", { name: "Validate" }).click();
  await expect(page.getByText(/^Valid\. References: /)).toBeVisible();

  await page.getByRole("button", { name: "Run" }).click();
  await expect(page.getByText(/ rows in \d+ms/)).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("columnheader", { name: "name" })).toBeVisible();

  await page.getByRole("button", { name: "Send to Chat/Chart" }).click();
  await page.waitForURL(/\/chat\?/);
  await expect(page.getByRole("heading", { name: "Chat" })).toBeVisible();
  await expect(
    page.getByPlaceholder("Ask a follow-up question or specify a slice...")
  ).toHaveValue(/Chart these results: select id, name from sales\.regions order by id/);
});

test("running above the export row threshold prompts step-up", async ({ page }) => {
  await signIn(page, "demo-developer");
  await page.goto("/sql");

  await fillSqlEditor(page, "select * from sales.regions");
  await page.getByLabel("Row limit").selectOption("25000");
  await page.getByRole("button", { name: "Run" }).click();

  // No factor enrolled yet: refused with instructions, not a crash (the same
  // pattern api-keys.spec.ts/data-sources.spec.ts prove for other step-up
  // actions).
  await expect(page.getByText("Add a two-factor method")).toBeVisible();

  await page.goto("/account");
  await page.getByRole("button", { name: "Add authenticator app" }).click();
  const secret = (await page.locator("span.font-mono").first().innerText()).trim();
  await page.getByLabel("Verification code").fill(totp(secret));
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByText("Authenticator app added.")).toBeVisible();

  await page.goto("/sql");
  await fillSqlEditor(page, "select * from sales.regions");
  await page.getByLabel("Row limit").selectOption("25000");
  await page.getByRole("button", { name: "Run" }).click();
  await expect(page.getByText(/ rows in \d+ms/)).toBeVisible({ timeout: 15_000 });
});

test("a client-role user never reaches SQL Lab", async ({ page }) => {
  await signIn(page, "demo-client");
  await expect(page.getByRole("link", { name: "SQL Lab" })).toHaveCount(0);

  await page.goto("/sql");
  await expect(page.getByText("Your role does not include access to SQL Lab.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Run" })).toHaveCount(0);
});
