import { expect, test, type Page } from "@playwright/test";

function watchBrowserFailures(page: Page) {
  const failures: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") failures.push(message.text());
  });
  page.on("pageerror", (error) => failures.push(error.message));
  return failures;
}

/** Picks a seeded scene on the hero and starts the trace. */
async function startScene(page: Page, label: string) {
  await page.goto("/");
  await page.getByRole("button", { name: new RegExp(label, "i") }).click();
  await page.getByRole("button", { name: "Send through Interlock" }).click();
  await expect(page.getByRole("heading", { name: "Pre-flight" })).toBeVisible();
}

/**
 * Jumps with the keyboard: hovering the rail slides the expanded overlay over
 * the collapsed bars, so a pointer click would land on the overlay instead.
 */
async function jumpToStage(page: Page, name: string) {
  await page.getByRole("button", { name: new RegExp(`^Stage \\d+, ${name}$`) }).press("Enter");
}

test.describe("demo traces", () => {
  test("a clean request is priced at L0 and released unchanged", async ({ page }, testInfo) => {
    const failures = watchBrowserFailures(page);
    await startScene(page, "Branch hours");

    await jumpToStage(page, "Pricing the ladder");
    await expect(page.getByText("cheapest safe action wins")).toBeVisible();
    await expect(page.getByText("₹2", { exact: true }).first()).toBeVisible();

    await jumpToStage(page, "Release");
    await expect(page.getByText("L0 PASS", { exact: true })).toBeVisible();
    await expect(page.getByText("What would have shipped without Interlock")).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("release-l0.png"), fullPage: true });
    expect(failures).toEqual([]);
  });

  test("the repair scene keeps every priced alternative visible", async ({ page }) => {
    const failures = watchBrowserFailures(page);
    await startScene(page, "Invented loan clause");

    await jumpToStage(page, "Pricing the ladder");
    for (const level of ["L0", "L1", "L2", "L3", "L4", "L5"]) {
      await expect(page.getByText(level, { exact: true }).first()).toBeVisible();
    }
    await expect(page.getByText("Chosen", { exact: true })).toBeVisible();

    await jumpToStage(page, "Release");
    await expect(page.getByText("L2 REPAIR", { exact: true })).toBeVisible();
    expect(failures).toEqual([]);
  });

  test("the held scene freezes the tool call at the gate", async ({ page }) => {
    const failures = watchBrowserFailures(page);
    await startScene(page, "Untrusted claim");

    await jumpToStage(page, "Commit gate");
    await expect(page.getByText("frozen at the interlock")).toBeVisible();
    await jumpToStage(page, "Release");
    await expect(page.getByText("L4 HOLD", { exact: true })).toBeVisible();
    expect(failures).toEqual([]);
  });

  test("the canary scene blocks deterministically and releases nothing", async ({ page }) => {
    const failures = watchBrowserFailures(page);
    await startScene(page, "Canary leak");

    await jumpToStage(page, "Pricing the ladder");
    await expect(page.getByText(/hard rule: canary token present/i)).toBeVisible();
    await jumpToStage(page, "Release");
    await expect(page.getByText("L5 BLOCK", { exact: true })).toBeVisible();
    await expect(page.getByText(/I cannot share internal payment references/)).toBeVisible();
    expect(failures).toEqual([]);
  });

  test("stage navigation works from the keyboard and the footer", async ({ page }) => {
    const failures = watchBrowserFailures(page);
    await startScene(page, "Branch hours");

    await page.keyboard.press("ArrowRight");
    await expect(page.getByRole("heading", { name: "Generation" })).toBeVisible();
    await page.keyboard.press("ArrowLeft");
    await expect(page.getByRole("heading", { name: "Pre-flight" })).toBeVisible();

    await page.getByRole("button", { name: "Pause" }).click();
    await expect(page.getByRole("button", { name: "Resume" })).toBeVisible();
    expect(failures).toEqual([]);
  });
});

test.describe("live backend", () => {
  test("a live stream drives the same stages and never leaks a resume token", async ({ page, request, baseURL }) => {
    const failures = watchBrowserFailures(page);
    await page.goto("/");
    await page.getByRole("button", { name: /Untrusted claim/i }).click();
    await page.getByRole("button", { name: /demo trace/i }).click();
    await expect(page.getByRole("button", { name: /live backend/i })).toBeVisible();

    await page.getByRole("button", { name: "Send through Interlock" }).click();
    // The run has started; the stream may already have advanced past stage 01.
    await expect(page.getByRole("button", { name: /^Stage 01/ })).toBeVisible();

    // The stream itself advances the stages; no fixture timeline is involved.
    await expect(page.getByRole("heading", { name: /In-flight checks|Pricing the ladder|Commit gate|Release/ })).toBeVisible({
      timeout: 20_000,
    });
    // Lane A latencies are not itemised in the stream contract, and the live
    // view says so rather than showing invented per-check numbers.
    await jumpToStage(page, "Pre-flight");
    await expect(page.getByText("AGGREGATE ONLY", { exact: true })).toBeVisible();

    const projection = await request.get(`${baseURL}/console/holds`);
    expect(await projection.text()).not.toContain("resume_token");
    expect(await page.locator("body").textContent()).not.toContain("replay-token-0001");
    expect(
      await page.evaluate(() => JSON.stringify({ local: localStorage, session: sessionStorage })),
    ).not.toContain("replay-token-0001");
    expect(failures).toEqual([]);
  });

  test("the review queue and evidence ledger read the real projections", async ({ page }) => {
    const failures = watchBrowserFailures(page);
    await page.goto("/");

    await page.getByRole("button", { name: /Reviews/ }).click();
    await expect(page.getByRole("heading", { name: "Pending reviews" })).toBeVisible();

    await page.getByRole("button", { name: /Evidence/ }).click();
    await expect(page.getByRole("heading", { name: "Evidence ledger" })).toBeVisible();
    await expect(page.getByText("Pre-action catch rate")).toBeVisible();
    await expect(page.getByText("Per-signal AUROC")).toBeVisible();
    expect(failures).toEqual([]);
  });
});
