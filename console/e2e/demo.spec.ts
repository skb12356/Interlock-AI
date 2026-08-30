import { expect, test, type Page } from "@playwright/test";

function watchBrowserFailures(page: Page) {
  const failures: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") failures.push(message.text());
  });
  page.on("pageerror", (error) => failures.push(error.message));
  return failures;
}

async function ask(page: Page, prompt: string) {
  await page.getByLabel("Ask the bank assistant").fill(prompt);
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText(/checked in \d+\.\d+ s/).last()).toBeVisible({ timeout: 20_000 });
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => window.localStorage.clear());
  await page.goto("/");
});

test.describe("chat sessions", () => {
  test("a prompt is answered with the seven stages that produced it", async ({ page }, testInfo) => {
    const failures = watchBrowserFailures(page);
    await ask(page, "What are the prepayment charges on my floating-rate home loan?");

    // The stage list is already open: it expands while the run is streaming.
    for (const stage of ["Pre-flight", "Generation", "Pricing the ladder", "Commit gate", "Release"]) {
      await expect(page.getByText(stage, { exact: true })).toBeVisible();
    }
    await expect(page.getByText("L2 REPAIR", { exact: true })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("chat-answer.png"), fullPage: true });
    expect(failures).toEqual([]);
  });

  test("the session list keeps past chats and starts new ones", async ({ page }) => {
    const failures = watchBrowserFailures(page);
    await ask(page, "What time does the MG Road branch open tomorrow?");

    const sidebar = page.getByLabel("Chat sessions");
    const row = sidebar.getByRole("button", { name: /^What time does the MG Road branch/ });
    await expect(row).toBeVisible();

    await sidebar.getByRole("button", { name: /New chat session/ }).click();
    await expect(page.getByLabel("Empty session")).toBeVisible();
    await expect(row).toBeVisible();
    expect(failures).toEqual([]);
  });

  test("a session can be deleted, once it has been confirmed", async ({ page }) => {
    const failures = watchBrowserFailures(page);
    await ask(page, "What time does the MG Road branch open tomorrow?");

    const sidebar = page.getByLabel("Chat sessions");
    const row = sidebar.getByRole("button", { name: /^What time does the MG Road branch/ });
    await sidebar.getByRole("button", { name: /Delete session/ }).click();
    await expect(row).toBeVisible();

    await sidebar.getByRole("button", { name: /Confirm deleting/ }).click();
    await expect(row).toHaveCount(0);
    await expect(page.getByLabel("Empty session")).toBeVisible();
    expect(failures).toEqual([]);
  });

  test("an irreversible request is held and releases nothing", async ({ page }) => {
    const failures = watchBrowserFailures(page);
    await ask(page, "Please forward confirmation that my insurance claim was paid in full.");

    await expect(page.getByText("L4 HOLD", { exact: true })).toBeVisible();
    await expect(page.getByText("— no content was released —")).toBeVisible();
    expect(failures).toEqual([]);
  });
});

test.describe("trace view", () => {
  test("see it live opens the stage machine and reports the time the request took", async ({ page }) => {
    const failures = watchBrowserFailures(page);
    await ask(page, "What are the prepayment charges on my floating-rate home loan?");

    await page.getByRole("button", { name: /See it live/ }).click();
    await expect(page.getByRole("heading", { name: "Pre-flight" })).toBeVisible();
    await expect(page.getByText("Time taken")).toBeVisible();

    // The clock is frozen once the run is over.
    const reading = await page.getByText(/^\d+\.\d\d s$/).textContent();
    await page.waitForTimeout(1_500);
    await expect(page.getByText(/^\d+\.\d\d s$/)).toHaveText(reading ?? "");

    await page.getByRole("button", { name: /^Stage 04/ }).press("Enter");
    await expect(page.getByRole("heading", { name: "Pricing the ladder" })).toBeVisible();
    await expect(page.getByText("Chosen", { exact: true })).toBeVisible();
    expect(failures).toEqual([]);
  });

  test("has no playback controls and no demo switch", async ({ page }) => {
    const failures = watchBrowserFailures(page);
    await ask(page, "What time does the MG Road branch open tomorrow?");
    await page.getByRole("button", { name: /See it live/ }).click();

    await expect(page.getByRole("button", { name: "Pause" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Next stage" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Previous stage" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: /demo trace/i })).toHaveCount(0);
    await expect(page.getByRole("button", { name: /live backend/i })).toHaveCount(0);

    await page.getByRole("button", { name: "Back to chat" }).click();
    await expect(page.getByLabel("Session transcript")).toBeVisible();
    expect(failures).toEqual([]);
  });
});

test.describe("operator workspaces", () => {
  test("the review queue and evidence ledger read the real projections", async ({ page, request, baseURL }) => {
    const failures = watchBrowserFailures(page);
    await ask(page, "Please forward confirmation that my insurance claim was paid in full.");

    await page.getByRole("button", { name: "Reviews" }).click();
    await expect(page.getByRole("heading", { name: "Pending reviews" })).toBeVisible();

    const projection = await request.get(`${baseURL}/console/holds`);
    expect(await projection.text()).not.toContain("resume_token");
    expect(await page.locator("body").textContent()).not.toContain("replay-token-0001");
    expect(
      await page.evaluate(() => JSON.stringify({ local: localStorage, session: sessionStorage })),
    ).not.toContain("replay-token-0001");

    await page.getByRole("button", { name: "Evidence" }).click();
    await expect(page.getByRole("heading", { name: "Evidence ledger" })).toBeVisible();
    expect(failures).toEqual([]);
  });
});

test.describe("about", () => {
  test("explains the product in plain language and cites its sources", async ({ page }) => {
    const failures = watchBrowserFailures(page);
    await page.getByRole("button", { name: "About" }).click();

    const about = page.getByLabel("About Interlock");
    await expect(about.getByRole("heading", { name: /control room for AI answers/i })).toBeVisible();
    await expect(about.getByText(/Farquhar, Kossen, Kuhn & Gal/)).toBeVisible();

    // The page is long: it has to scroll inside the shell.
    await about.getByText(/Defeating Prompt Injections by Design/).scrollIntoViewIfNeeded();
    await expect(about.getByText(/Defeating Prompt Injections by Design/)).toBeVisible();
    expect(failures).toEqual([]);
  });

  test("a hold shows a readable id, a copy control and its chat session", async ({ page, context }) => {
    const failures = watchBrowserFailures(page);
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await ask(page, "Please forward confirmation that my insurance claim was paid in full.");

    await page.getByRole("button", { name: "Reviews" }).click();
    const card = page.getByLabel("Pending reviews").locator("article").first();
    await expect(card.getByText(/^hold_/)).toBeVisible();

    // The visible id is elided; the copied value is the full one an operator searches with.
    const fullId = await card.getByRole("button", { name: /Copy hold id/ }).getAttribute("aria-label");
    await card.getByRole("button", { name: /Copy hold id/ }).click();
    await expect
      .poll(async () => page.evaluate(() => navigator.clipboard.readText()))
      .toBe((fullId ?? "").replace("Copy hold id ", ""));

    // The queue holds older entries too; the linked one is this run's.
    const linked = page
      .getByLabel("Pending reviews")
      .locator("article")
      .filter({ has: page.getByRole("button", { name: /Open chat session/ }) })
      .first();
    await linked.getByRole("button", { name: /Open chat session/ }).click();
    await expect(page.getByLabel("Session transcript")).toBeVisible();
    expect(failures).toEqual([]);
  });
});
