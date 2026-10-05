#!/usr/bin/env node
// Browser journey test — the click-through a human checker would do, automated.
//   npm i -D playwright   (once, anywhere; or set PLAYWRIGHT_MODULE=/path/to/node_modules/playwright)
//   npx playwright install chromium   (if you have no browser)  →  node scripts/e2e.mjs
// It builds nothing: run `make build` first. It starts its own OpenRouter replay server + platform on free ports.
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import net from "node:net";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(import.meta.url);
let chromium;
for (const m of [process.env.PLAYWRIGHT_MODULE, "playwright", "playwright-core", "/opt/node22/lib/node_modules/playwright"].filter(Boolean)) {
  try { ({ chromium } = require(m)); break; } catch { /* try next */ }
}
if (!chromium) { console.error("playwright not found — see header of scripts/e2e.mjs"); process.exit(2); }

const freePort = () => new Promise((res) => { const s = net.createServer().listen(0, () => { const p = s.address().port; s.close(() => res(p)); }); });
const procs = [];
const start = (cmd, args, env, cwd) => { const p = spawn(cmd, args, { cwd, env: { ...process.env, ...env }, stdio: "ignore" }); procs.push(p); return p; };
const until = async (url, ms = 30000) => { const t = Date.now(); while (Date.now() - t < ms) { try { if ((await fetch(url)).ok) return; } catch {} await new Promise((r) => setTimeout(r, 200)); } throw new Error(`timeout waiting for ${url}`); };

let failures = 0;
const only = process.env.E2E_ONLY;              // run only steps whose name contains this text
const shotDir = process.env.E2E_SHOTS;          // save a screenshot whenever a step fails
let page;
const step = async (name, fn) => {
  if (only && !name.includes(only)) return;
  try { await fn(); console.log(`  ✓ ${name}`); } catch (e) {
    failures++; console.log(`  ✗ ${name}\n      ${String(e.message).split("\n")[0]}`);
    if (shotDir && page) await page.screenshot({ path: path.join(shotDir, `fail-${name.replace(/\W+/g, "-").slice(0, 50)}.png`) }).catch(() => {});
  }
};
const expect = (cond, msg) => { if (!cond) throw new Error(msg); };

const rp = await freePort(), ap = await freePort();
const data = mkdtempSync(path.join(tmpdir(), "evalplatform-e2e-"));
start("python3", ["-m", "evalplatform.devtools.replay_server", "--port", String(rp), "--speed", "8", "--key", "e2e-key", "--slow-variant"], { PYTHONPATH: path.join(root, "backend") }, root);
start("python3", ["-m", "evalplatform", "--port", String(ap)], {
  PYTHONPATH: path.join(root, "backend"), EVAL_DATA_DIR: data, EVAL_DEFAULT_PROVIDER: "openrouter", OPENROUTER_API_KEY: "e2e-key",
  EVAL_OPENROUTER_BASE_URL: `http://127.0.0.1:${rp}/api/v1`,
}, root);
const ap2 = await freePort();   // second instance WITHOUT a server-side key → the user must paste one in the UI
start("python3", ["-m", "evalplatform", "--port", String(ap2)], {
  PYTHONPATH: path.join(root, "backend"), EVAL_DATA_DIR: mkdtempSync(path.join(tmpdir(), "evalplatform-e2e2-")), EVAL_DEFAULT_PROVIDER: "openrouter", OPENROUTER_API_KEY: "",
  EVAL_OPENROUTER_BASE_URL: `http://127.0.0.1:${rp}/api/v1`,
}, root);
const BASE = `http://127.0.0.1:${ap}`;
const BASE2 = `http://127.0.0.1:${ap2}`;
await until(`${BASE}/api/health`); await until(`${BASE2}/api/health`); await until(`http://127.0.0.1:${rp}/api/v1/models`);

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 }, colorScheme: "dark" });
page = await ctx.newPage();
const pageErrors = [];
page.on("pageerror", (e) => pageErrors.push(e.message));
const pick = async (query, name) => {
  await page.click(".picker-trigger"); await page.fill(".picker-search input", query); await page.waitForTimeout(250);
  await page.click(`.picker-item:has-text("${name}")`);
};

console.log("Journey:");
await step("home renders hero, provider and the dropdown", async () => {
  await page.goto(BASE, { waitUntil: "networkidle" });
  expect(await page.isVisible("h1"), "no hero heading");
  expect((await page.textContent(".pill")).includes("OpenRouter"), "provider pill missing");
});
await step("dropdown lists live OpenRouter models with prices", async () => {
  await page.click(".picker-trigger"); await page.waitForSelector(".picker-item");
  const n = await page.locator(".picker-item").count();
  expect(n >= 4, `expected ≥4 models, got ${n}`);
  expect((await page.textContent(".picker-menu")).includes("/"), "no pricing shown");
  await page.keyboard.press("Escape"); await page.click("h1");
});
let runUrl;
await step("selecting a model starts a run immediately and shows live progress", async () => {
  await page.click('button[aria-label="Quick mode"]').catch(async () => { await page.click(".opts-toggle"); await page.click('button[aria-label="Quick mode"]'); });
  await pick("sonnet", "Sonnet (recorded");
  await page.waitForURL(/\/runs\//);
  runUrl = page.url();
  await page.waitForSelector(".stepper .step");
  expect((await page.locator(".stepper .step").count()) === 9, "stepper should have 9 phases");
});
await step("report appears with verdict, scores, cost card and recommendations", async () => {
  await page.waitForSelector(".verdict", { timeout: 120000 });
  expect((await page.textContent(".verdict h2")).includes("Ready"), "verdict should be Ready for a strong model");
  const text = await page.textContent("body");
  for (const s of ["Coherency", "Coding", "Cost & provider", "Speculative decoding", "Implementation steps", "Hosted API run"]) expect(text.includes(s), `missing section: ${s}`);
  expect((await page.locator(".tabs button").count()) === 3, "recommendation tabs missing");
});
await step("recommendation tabs switch and test rows expand with evidence", async () => {
  await page.click(".tabs button:has-text('Make it more coherent')"); await page.waitForSelector(".rec");
  await page.click(".tabs button:has-text('Make it faster')"); await page.waitForSelector(".rec");
  await page.locator("tr.t-row").first().click(); await page.waitForSelector(".detail .resp");
  expect((await page.textContent(".detail")).includes("Prompt"), "expanded row lacks prompt");
});
await step("exports work (markdown + json) and never contain the API key", async () => {
  const id = runUrl.split("/runs/")[1];
  const md = await (await fetch(`${BASE}/api/runs/${id}/report.md`)).text();
  const js = await (await fetch(`${BASE}/api/runs/${id}/report.json`)).text();
  expect(md.startsWith("# Evaluation report"), "markdown export broken");
  expect(!md.includes("e2e-key") && !js.includes("e2e-key"), "API key leaked into export");
});
await step("page reload on a finished run restores the report", async () => {
  await page.reload({ waitUntil: "networkidle" }); await page.waitForSelector(".verdict");
});
await step("stress control run shows the detector self-check banner", async () => {
  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.click(".opts-toggle");
  await page.selectOption("select >> nth=-1", "garble");
  await page.click('button[aria-label="Quick mode"]');
  await pick("sonnet", "Sonnet (recorded");
  await page.waitForSelector(".verdict", { timeout: 120000 });
  const t = await page.textContent("body");
  expect(t.includes("Detector self-check") && t.includes("Working as intended"), "self-check banner missing");
  expect((await page.textContent(".verdict h2")).includes("Not ready"), "garbled run must be Not ready");
});
await step("compare mode: 3 models → leaderboard, radar, takeaways", async () => {
  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.click(".opts-toggle");
  await page.click('button[aria-label="Compare mode"]'); await page.click('button[aria-label="Quick mode"]');
  await page.click(".picker-trigger");
  for (const n of ["haiku", "sonnet", "tiny"]) await page.click(`.picker-item:has-text("${n.charAt(0).toUpperCase()+n.slice(1)} (")`);
  await page.click("text=/Evaluate 3 models/");
  await page.waitForURL(/\/compare\?ids=/);
  await page.waitForFunction(() => document.querySelectorAll("table.cmp tbody tr").length === 3, null, { timeout: 180000 });
  const t = await page.textContent("body");
  expect(t.includes("Takeaways") && t.includes("Leaderboard"), "compare sections missing");
  const firstRow = await page.locator("table.cmp tbody tr").first().textContent();
  expect(!firstRow.includes("tiny"), "the emulated weak model must not rank first");
  const lastRow = await page.locator("table.cmp tbody tr").last().textContent();
  expect(lastRow.includes("tiny"), "the emulated weak model should rank last");
});
await step("history selection → compare button", async () => {
  await page.goto(`${BASE}/history`, { waitUntil: "networkidle" });
  const boxes = page.locator(".cbox:not([disabled])");
  await boxes.nth(0).click(); await boxes.nth(1).click();
  await page.click("button:has-text('Compare 2 runs')"); await page.waitForURL(/\/compare\?ids=/);
});
await step("theme toggle switches and persists", async () => {
  const before = await page.evaluate(() => document.documentElement.dataset.theme);
  await page.click('button[aria-label^="Switch to"]');
  const after = await page.evaluate(() => document.documentElement.dataset.theme);
  expect(before !== after, "theme did not change");
  await page.reload({ waitUntil: "networkidle" });
  expect((await page.evaluate(() => document.documentElement.dataset.theme)) === after, "theme not persisted");
});
await step("no server key: UI asks for a key, rejects a start without one, then works with a pasted key (kept out of storage)", async () => {
  const ctx2 = await browser.newContext({ viewport: { width: 1280, height: 900 }, colorScheme: "dark" });
  const p2 = await ctx2.newPage();
  await p2.goto(BASE2, { waitUntil: "networkidle" });
  expect((await p2.textContent("body")).includes("OpenRouter key needed"), "missing 'key needed' banner");
  await p2.click(".picker-trigger"); await p2.fill(".picker-search input", "haiku"); await p2.waitForTimeout(250);
  await p2.click(".picker-item:has-text('Haiku (recorded')");
  await p2.waitForSelector(".banner.err", { timeout: 10000 });
  expect((await p2.textContent(".banner.err")).toLowerCase().includes("key"), "should explain that a key is required");
  await p2.waitForSelector("input[type=password]");
  await p2.fill("input[type=password]", "e2e-key");
  await p2.click('button[aria-label="Quick mode"]');
  await p2.click(".picker-trigger"); await p2.fill(".picker-search input", "haiku"); await p2.waitForTimeout(250);
  await p2.click(".picker-item:has-text('Haiku (recorded')");
  await p2.waitForSelector(".verdict", { timeout: 120000 });
  const id = p2.url().split("/runs/")[1];
  const stored = await (await fetch(`${BASE2}/api/runs/${id}`)).text();
  expect(!stored.includes("e2e-key"), "key leaked into stored run");
  const ls = await p2.evaluate(() => JSON.stringify(localStorage));
  expect(!ls.includes("e2e-key"), "key must not be written to localStorage");
  await ctx2.close();
});
await step("a failing run (bad key) shows a clear error, not a blank page", async () => {
  const r = await fetch(`${BASE}/api/runs`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ openrouter_model: "replay/haiku", openrouter_key: "wrong-key" }) });
  const { run_id } = await r.json();
  await page.goto(`${BASE}/runs/${run_id}`, { waitUntil: "networkidle" });
  await page.waitForSelector(".banner.err", { timeout: 30000 });
  expect((await page.textContent(".banner.err")).toLowerCase().includes("api key"), "error banner should mention the key");
});
await step("cancelling a running evaluation works", async () => {
  const r = await fetch(`${BASE}/api/runs`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ openrouter_model: "replay/opus:slow" }) });
  const { run_id } = await r.json();
  await page.goto(`${BASE}/runs/${run_id}`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector("button:has-text('Cancel')", { timeout: 15000 });
  await page.click("button:has-text('Cancel')");
  await page.waitForSelector(".banner.err:has-text('cancelled')", { timeout: 20000 });
});
await step("invalid input is rejected with a message (hosted + speculative)", async () => {
  const r = await fetch(`${BASE}/api/runs`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ openrouter_model: "replay/haiku", speculative: "ngram" }) });
  expect(r.status === 422, `expected 422, got ${r.status}`);
});
await step("mobile layout has no horizontal overflow on any main screen", async () => {
  const m = await (await browser.newContext({ viewport: { width: 390, height: 844 }, colorScheme: "dark" })).newPage();
  for (const p of ["/", "/history", `/compare?ids=${(await (await fetch(`${BASE}/api/runs`)).json()).slice(0, 2).map((r) => r.id).join(",")}`]) {
    await m.goto(BASE + p, { waitUntil: "networkidle" }); await m.waitForTimeout(400);
    const w = await m.evaluate(() => document.documentElement.scrollWidth);
    expect(w <= 392, `${p} overflows horizontally (${w}px)`);
  }
});
await step("no uncaught JavaScript errors during the whole journey", async () => { expect(pageErrors.length === 0, pageErrors.join(" | ")); });

await browser.close();
procs.forEach((p) => p.kill());
console.log(failures ? `\n${failures} step(s) FAILED` : "\nAll journey steps passed");
process.exit(failures ? 1 : 0);
