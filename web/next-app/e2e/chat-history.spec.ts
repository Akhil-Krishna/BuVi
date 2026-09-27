import { test, expect } from "@playwright/test";
import { clearMfaAndSessions, DEMO } from "./reset";
import { signIn } from "./sign-in";

test.beforeAll(() => clearMfaAndSessions(DEMO.developer));

test("chat history: sidebar lists conversations, is clickable, and resumes the same thread", async ({
  page,
}) => {
  await signIn(page, "demo-developer");
  await page.goto("/chat");

  const first = `sidebar test ${Date.now()}`;
  await page.getByPlaceholder("Ask a question about your data...").fill(first);
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByRole("button", { name: new RegExp(first) })).toBeVisible({
    timeout: 15_000,
  });

  // Starting a new chat clears the transcript but keeps the sidebar entry.
  await page.getByRole("button", { name: "+ New chat" }).click();
  const thread = page.getByTestId("chat-thread");
  await expect(thread.getByText(first, { exact: true })).toHaveCount(0);
  const sidebarEntry = page.getByRole("button", { name: new RegExp(first) });
  await expect(sidebarEntry).toBeVisible();

  // Clicking it resumes the exact same conversation: the original prompt reappears.
  await sidebarEntry.click();
  await expect(thread.getByText(first, { exact: true })).toBeVisible();
  await expect(sidebarEntry).toHaveAttribute("aria-current", "true");
});
