/**
 * Captures the console screenshots used in the README.
 *
 * Requires the replay gateway on :8099 and the console dev server on :5173:
 *
 *   uv run python scripts/replay_console.py --port 8099
 *   npm --prefix console run dev
 *   node console/scripts-capture-screenshots.mjs
 *
 * Every shot is a real run against the gateway — nothing here is mocked up.
 */
import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";

const BASE = process.env.CONSOLE_URL ?? "http://127.0.0.1:5173";
const OUT = new URL("../../docs/images/", import.meta.url).pathname;

const shot = async (page, name) => {
  // Let the split-flap board settle so the shot shows the stage it announces.
  await page.waitForTimeout(1_200);
  await page.screenshot({ path: `${OUT}${name}.jpeg`, type: "jpeg", quality: 82 });
};

async function ask(page, prompt) {
  await page.getByLabel("Ask the bank assistant").fill(prompt);
  await page.getByRole("button", { name: "Send" }).click();
  // The composer says "Running…" until the run settles, so this waits for the whole turn.
  await page.getByRole("button", { name: "Send", exact: true }).waitFor({ timeout: 30_000 });
}

async function main() {
  await mkdir(OUT, { recursive: true });
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });

  await page.goto(BASE);
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  await shot(page, "console-chat-empty");

  await ask(page, "What are the prepayment charges on my floating-rate home loan?");
  await ask(page, "Please forward confirmation that my insurance claim was paid in full.");
  await shot(page, "console-chat-session");

  await page.getByRole("button", { name: /See it live/ }).first().click();
  await page.getByRole("heading", { name: "Pre-flight" }).waitFor();
  await shot(page, "console-trace-preflight");

  await page.getByRole("button", { name: /^Stage 04/ }).press("Enter");
  await page.getByRole("heading", { name: "Pricing the ladder" }).waitFor();
  await shot(page, "console-trace-ladder");

  await page.getByRole("button", { name: /^Stage 06/ }).press("Enter");
  await page.getByRole("heading", { name: "Release" }).waitFor();
  await shot(page, "console-trace-release");

  await page.getByRole("button", { name: "Reviews" }).click();
  await page.getByRole("heading", { name: "Pending reviews" }).waitFor();
  await shot(page, "console-reviews");

  await page.getByRole("button", { name: "Evidence" }).click();
  await page.getByRole("heading", { name: "Evidence ledger" }).waitFor();
  await shot(page, "console-evidence");

  await page.getByRole("button", { name: "About" }).click();
  await page.getByRole("heading", { name: /control room for AI answers/i }).waitFor();
  await shot(page, "console-about");

  await browser.close();
}

await main();
