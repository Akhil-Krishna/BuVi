import { createHmac } from "node:crypto";
import { test, expect } from "@playwright/test";
import { clearMfaAndSessions, DEMO } from "./reset";
import { signIn } from "./sign-in";

function totp(secret: string, atMs = Date.now()): string {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const c of secret.replace(/=+$/, "").toUpperCase()) {
    const i = alphabet.indexOf(c); if (i === -1) continue;
    bits += i.toString(2).padStart(5, "0");
  }
  const key = Buffer.from((bits.match(/.{8}/g) ?? []).map((b) => parseInt(b, 2)));
  const counter = Math.floor(atMs / 1000 / 30);
  const msg = Buffer.alloc(8);
  msg.writeUInt32BE(Math.floor(counter / 2 ** 32), 0); msg.writeUInt32BE(counter >>> 0, 4);
  const d = createHmac("sha1", key).update(msg).digest();
  const o = d[d.length - 1] & 0x0f;
  const bin = ((d[o] & 0x7f) << 24) | ((d[o+1] & 0xff) << 16) | ((d[o+2] & 0xff) << 8) | (d[o+3] & 0xff);
  return (bin % 1_000_000).toString().padStart(6, "0");
}

test.beforeAll(() => clearMfaAndSessions(DEMO.admin));

// The whole point: the browser must NOT share the Node server's locale/time zone, or a
// locale-dependent format agrees by luck and the test passes with the bug still present
// (checked -- it did). en-GB + IST is the reporter's own environment.
test.use({ locale: "en-GB", timezoneId: "Asia/Kolkata" });

// The reported bug: a confirmed MFA factor's date was formatted with the host's locale, so the
// server said "Sep 26, 2026" and an en-GB browser said "26 Sept 2026".
test("no hydration mismatch on the pages that render server-side dates", async ({ page }) => {
  const problems: string[] = [];
  page.on("console", (m) => {
    const t = m.text();
    if (/hydrat|did not match|server rendered/i.test(t)) problems.push(t);
  });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));

  await signIn(page, "demo-admin");

  // Enrol a factor so MfaPanel actually renders a confirmed_at date -- with no factor the buggy
  // line never runs and the test would pass vacuously.
  await page.goto("/account");
  await page.getByRole("button", { name: "Add authenticator app" }).click();
  const secret = (await page.locator("span.font-mono").first().innerText()).trim();
  await page.getByLabel("Verification code").fill(totp(secret));
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByText("Authenticator app added.")).toBeVisible();

  await page.goto("/account");           // fresh SSR with a confirmed_at present
  await expect(page.getByText(/UTC|Pending/).first()).toBeVisible();
  for (const path of ["/audit", "/billing", "/data", "/dashboards"]) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
  }

  expect(problems, `console/page errors:\n${problems.join("\n")}`).toHaveLength(0);
});
