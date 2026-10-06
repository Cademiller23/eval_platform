#!/usr/bin/env node
// Click-through test of the offline preview (docs/coherence-lab-preview.html) — no server involved.
//   make preview && node scripts/e2e_preview.mjs        (screenshots: E2E_SHOTS=/some/dir)
// Playwright is located the same way as in scripts/e2e.mjs.
import { createRequire } from "node:module";
import { existsSync, mkdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const file = path.join(root, "docs", "coherence-lab-preview.html");
if (!existsSync(file)) { console.error("docs/coherence-lab-preview.html is missing — run `make preview` first"); process.exit(2); }
const require = createRequire(import.meta.url);
let chromium;
for (const m of [process.env.PLAYWRIGHT_MODULE, "playwright", "playwright-core", "/opt/node22/lib/node_modules/playwright"].filter(Boolean)) {
  try { ({ chromium } = require(m)); break; } catch { /* try next */ }
}
if (!chromium) { console.error("playwright not found — see header of scripts/e2e.mjs"); process.exit(2); }

const shots = process.env.E2E_SHOTS;
if (shots) mkdirSync(shots, { recursive: true });
const URL_ = pathToFileURL(file).href;
let failures = 0;
let page;
const step = async (name, fn) => {
  try { await fn(); console.log(`  ✓ ${name}`); } catch (e) {
    failures++; console.log(`  ✗ ${name}\n      ${String(e.message).split("\n")[0]}`);
    if (shots && page) await page.screenshot({ path: path.join(shots, `fail-${name.replace(/\W+/g, "-").slice(0, 50)}.png`) }).catch(() => {});
  }
};
const expect = (c, m) => { if (!c) throw new Error(m); };
const shot = async (name, full = false) => { if (shots) await page.screenshot({ path: path.join(shots, `${name}.png`), fullPage: full }); };

const browser = await chromium.launch();
const newPage = async (viewport = { width: 1280, height: 900 }, colorScheme = "light") => {
  const ctx = await browser.newContext({ viewport, colorScheme });
  const p = await ctx.newPage();
  await p.addInitScript(() => { window.__demoPlaybackMs = 6000; });   // a test hook: replay a run in 6 s instead of ~34 s
  return p;
};
page = await newPage();
const errors = [];
const external = [];
page.on("pageerror", (e) => errors.push(e.message));
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
page.on("request", (r) => { if (!/^(file|data|blob):/.test(r.url())) external.push(r.url()); });

const openOptions = async () => { if (!(await page.isVisible(".sheet"))) await page.click(".opts-toggle"); };
const pick = async (query, name) => {
  await page.click(".picker-trigger"); await page.fill(".picker-search input", query); await page.waitForTimeout(250);
  await page.click(`.picker-item:has-text("${name}")`);
};

console.log("Preview journey:");
await step("opens straight from the file, labelled as a preview, with history pre-filled", async () => {
  await page.goto(URL_, { waitUntil: "load" });
  await page.waitForSelector(".run-row");
  expect((await page.textContent("h1")).includes("ready to ship"), "hero missing");
  expect((await page.textContent(".nav")).includes("Interactive preview"), "preview badge missing");
  expect((await page.locator(".run-row").count()) >= 6, "history should be pre-filled");
  const cards = await page.locator(".feature h3").allTextContents();
  expect(cards.length === 8, `expected 8 "what every run measures" cards, got ${cards.length}`);
  for (const c of ["System prompts", "Hyperparameter tuning"]) expect(cards.includes(c), `missing feature card: ${c}`);
  await shot("01-home", true);
});
await step("dropdown lists the catalogue with details", async () => {
  await page.click(".picker-trigger"); await page.waitForSelector(".picker-item");
  const n = await page.locator(".picker-item").count();
  expect(n >= 20, `expected the full catalogue, got ${n}`);
  await page.waitForTimeout(500);   // let the menu finish its open animation
  await shot("02-dropdown");
  await page.keyboard.press("Escape"); await page.click("h1");
});
await step("options offer only what the preview can show", async () => {
  await openOptions();
  const labels = await page.locator(".seg button").allTextContents();
  expect(labels.join("|") === "Demo|OpenRouter", `providers: ${labels.join("|")}`);
  expect(await page.isDisabled('button[aria-label="Quick mode"]'), "quick mode should be disabled in the preview");
  for (const l of ["System-prompt suite", "Hyperparameter tuning"]) {
    expect((await page.locator(`button[aria-label="${l}"]`).count()) === 1, `missing option: ${l}`);
    expect(await page.isDisabled(`button[aria-label="${l}"]`), `${l} should be fixed on in the preview`);
  }
  await shot("03-options");
});
await step("choosing a model plays a live evaluation, then shows the report", async () => {
  await pick("llama 3.1 8b", "Llama 3.1 8B");
  await page.waitForSelector(".stepper .step");
  expect((await page.locator(".stepper .step").count()) === 11, "stepper should have 11 phases");
  const titles = await page.locator(".stepper .step .t").allTextContents();
  expect(titles.includes("System prompts") && titles.includes("Hyperparameters"), `stepper titles: ${titles.join("|")}`);
  await page.waitForSelector(".step.running");
  await page.waitForFunction(() => document.querySelectorAll(".tile").length > 3, null, { timeout: 20000 });
  await shot("04-live");
  await page.waitForSelector(".verdict", { timeout: 40000 });
  expect((await page.textContent(".verdict h2")).includes("Ready"), "verdict");
  const t = await page.textContent("body");
  for (const s of ["Coherency", "Coding", "Speculative decoding", "Implementation steps", "How the overall score is built"]) expect(t.includes(s), `missing section: ${s}`);
  expect((await page.locator(".score-card").count()) === 8, "eight score cards (seven axes + pass rate)");
  await shot("05-report", true);
});
await step("system-prompt section: KPIs, nine categories, and a test row opens with its conversation", async () => {
  await page.locator("#system-prompts").scrollIntoViewIfNeeded();
  expect((await page.locator("#system-prompts .kpi").count()) === 5, "system-prompt KPI strip");
  expect((await page.locator("#system-prompts .cat").count()) === 9, "nine categories");
  expect((await page.textContent("#system-prompts")).includes("Prompt-injection resistance"), "category label");
  await shot("05a-system-prompts");
  if (!(await page.locator("#system-prompts .tlist button:visible").count())) await page.locator("#system-prompts details.tgood summary").first().click();
  await page.locator("#system-prompts .tlist button:visible").first().click();      // failing tests are listed first; passing ones sit behind a disclosure
  await page.waitForSelector(".detail .conv .msg.system", { timeout: 5000 });
  expect((await page.locator(".detail .conv .msg").count()) >= 2, "conversation view should list the system and user messages");
});
await step("hyperparameter section: chart, profiles, honoured settings, held-out test", async () => {
  await page.locator("#hyperparameters").scrollIntoViewIfNeeded();
  expect((await page.locator("#hyperparameters .kpi").count()) === 5, "hyperparameter KPI strip");
  await page.waitForSelector("#hyperparameters .chart svg path");
  expect((await page.locator("#hyperparameters .chart svg path").count()) >= 3, "temperature chart should draw its series");
  expect((await page.locator("#hyperparameters .profile").count()) === 3, "three recommended profiles");
  expect((await page.locator("#hyperparameters .ctl").count()) >= 6, "honoured / ignored list");
  const t = await page.textContent("#hyperparameters");
  for (const s of ["Quality against temperature", "Recommended settings", "Do the tuned settings actually help?", "Reproducibility"]) expect(t.includes(s), `missing: ${s}`);
  await shot("05b-hyperparameters");
});
await step("recommendation tabs and test rows work", async () => {
  await page.click(".tabs button:has-text('Make it more coherent')"); await page.waitForSelector(".rec");
  await page.click(".tabs button:has-text('Make it faster')"); await page.waitForSelector(".rec");
  await page.click(".tabs button:has-text('Harden the system prompt')"); await page.waitForSelector(".rec");
  await page.click(".tabs button:has-text('Tune the sampling')"); await page.waitForSelector(".rec");
  await page.click(".filters button:has-text('System prompts')");
  expect((await page.locator("tr.t-row").count()) === 59, "the System prompts filter should list 59 tests");
  await page.click(".filters button:has-text('All')");
  await page.locator("tr.t-row").first().click(); await page.waitForSelector(".detail .resp");
});
await step("exports: the Markdown report comes from the recorded exporter", async () => {
  const id = (await page.textContent(".footer")).match(/Run ([0-9a-f]{12})/)?.[1];
  expect(id, "run id missing in the report footer");
  const md = await page.evaluate(async (rid) => (await fetch(`/api/runs/${rid}/report.md`)).text(), id);
  expect(md.startsWith("# Evaluation report") && md.includes("## Speculative decoding") && !md.includes("@@GENERATED@@"), "markdown export is wrong");
  expect(md.includes("## System prompts") && md.includes("## Hyperparameters"), "markdown export lacks the new sections");
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.click("button:has-text('Copy as Markdown')");     // clipboard may be refused in a sandbox; the UI must cope either way
  await page.waitForSelector("button:has-text('Copied'), .banner.err");
});
await step("re-running with the recommended recipe shows a before/after panel", async () => {
  await page.click("button:has-text('Re-run with EAGLE3')");
  await page.waitForSelector(".verdict", { timeout: 40000 });
  await page.waitForSelector(".delta");
  const t = await page.textContent(".delta");
  expect(/decode speed vs previous run/.test(t) && /baseline/.test(t) && /eagle3/.test(t), `delta panel: ${t.slice(0, 120)}`);
  if (shots) await (await page.$(".delta")).screenshot({ path: path.join(shots, "09-delta.png") });
});
await step("a speculative-decoding recipe changes the result (EAGLE-3: active)", async () => {
  await page.click(".brand"); await openOptions();
  await page.selectOption('select[aria-label="Speculative decoding"]', "eagle3");
  await pick("llama 3.1 8b", "Llama 3.1 8B");
  await page.waitForSelector(".verdict", { timeout: 40000 });
  const t = await page.textContent("body");
  expect(/eagle/i.test(t), "report should mention EAGLE");
  expect((await page.locator(".badge:has-text('Speculative: eagle3')").count()) > 0, "run header should show the recipe");
});
await step("unsupported combinations explain themselves", async () => {
  await page.click(".brand"); await openOptions();
  await page.selectOption('select[aria-label="Speculative decoding"]', "eagle3");
  await pick("smollm2", "SmolLM2");
  await page.waitForSelector(".banner.err");
  expect((await page.textContent(".banner.err")).includes("No eagle3 recipe"), "should explain why");
});
await step("cancelling a running evaluation works", async () => {
  await page.evaluate(() => { window.__demoPlaybackMs = 30000; });
  await page.click(".brand"); await openOptions();
  await page.selectOption('select[aria-label="Speculative decoding"]', "auto");
  await pick("qwen3 8b", "Qwen3 8B");
  await page.waitForSelector("button:has-text('Cancel')");
  await page.waitForSelector(".step.running");
  await page.click("button:has-text('Cancel')");
  await page.waitForSelector(".banner.err:has-text('cancelled')");
  await page.evaluate(() => { window.__demoPlaybackMs = 6000; });
});
await step("OpenRouter: real recorded answers, garble self-check is caught", async () => {
  await page.click(".brand"); await openOptions();
  await page.click('.seg button:has-text("OpenRouter")');
  await page.waitForSelector(".banner:has-text('Hosted API')");
  await page.selectOption('select[aria-label="Detector self-check"]', "garble");
  await pick("haiku", "Haiku (recorded");
  await page.waitForSelector(".verdict", { timeout: 40000 });
  const t = await page.textContent("body");
  expect(t.includes("Detector self-check") && t.includes("Working as intended"), "self-check banner missing");
  expect((await page.textContent(".verdict h2")).includes("Not ready"), "garbled run must be Not ready");
  await shot("06-selfcheck", true);
});
await step("history → select two → compare (leaderboard, radar, takeaways)", async () => {
  await page.click('a.pill:has-text("History")');
  await page.waitForSelector(".cbox:not([disabled])");
  const boxes = page.locator(".cbox:not([disabled])");
  await boxes.nth(1).click(); await boxes.nth(2).click(); await boxes.nth(3).click();
  await page.click("button:has-text('Compare 3 runs')");
  await page.waitForSelector("table.cmp tbody tr");
  expect((await page.locator("table.cmp tbody tr").count()) === 3, "three rows");
  const t = await page.textContent("body");
  expect(t.includes("Takeaways") && t.includes("Leaderboard"), "compare sections missing");
  const heads = (await page.locator("table.cmp thead th").allTextContents()).join("|");
  for (const h of ["System", "Sampling", "Injection"]) expect(heads.includes(h), `leaderboard lacks the ${h} column`);
  await shot("07-compare", true);
});
await step("compare mode from the home screen", async () => {
  await page.click(".brand"); await openOptions();
  await page.click('.seg button:has-text("Demo")');
  await page.click('button[aria-label="Compare mode"]');
  await page.click(".picker-trigger");
  await page.click('.picker-item:has-text("Llama 3.1 8B")'); await page.click('.picker-item:has-text("Qwen3 8B")');
  await page.click("text=/Evaluate 2 models/");
  await page.waitForSelector("table.cmp tbody tr", { timeout: 40000 });
  await page.waitForFunction(() => document.querySelectorAll("table.cmp tbody tr").length === 2 && !document.querySelector(".spinner"), null, { timeout: 40000 });
});
await step("unsupported inputs get a readable message", async () => {
  await page.click(".brand"); await openOptions();
  await page.click(".picker-trigger"); await page.fill(".picker-search input", "someorg/custom-model"); await page.waitForTimeout(250);
  await page.click(".picker-item"); await page.waitForSelector(".banner.err");
  expect((await page.textContent(".banner.err")).includes("preview"), "should say the preview can't do this");
});
await step("dark mode and phone width render without horizontal overflow", async () => {
  for (const [vp, scheme] of [[{ width: 390, height: 844 }, "dark"], [{ width: 390, height: 844 }, "light"], [{ width: 1280, height: 900 }, "dark"]]) {
    const p = await newPage(vp, scheme);
    await p.goto(URL_, { waitUntil: "load" }); await p.waitForSelector(".run-row");
    const w = await p.evaluate(() => document.documentElement.scrollWidth);
    expect(w <= vp.width + 2, `${scheme} ${vp.width}px overflows (${w}px)`);
    if (shots) await p.screenshot({ path: path.join(shots, `08-${scheme}-${vp.width}.png`), fullPage: true });
    // the report is the widest screen: a seeded run, with the new sections and the chart
    await p.locator(".run-row").first().click(); await p.waitForSelector(".verdict", { timeout: 20000 }); await p.waitForSelector("#hyperparameters .chart svg");
    await p.waitForTimeout(500);
    const rw = await p.evaluate(() => document.documentElement.scrollWidth);
    expect(rw <= vp.width + 2, `${scheme} ${vp.width}px: the report overflows (${rw}px)`);
    const chart = await p.evaluate(() => { const c = document.querySelector("#hyperparameters .chart svg"); const r = c.getBoundingClientRect(); return [Math.round(r.left), Math.round(r.right)]; });
    expect(chart[0] >= 0 && chart[1] <= vp.width, `${scheme} ${vp.width}px: the chart is cut off (${chart})`);
    if (shots) await p.screenshot({ path: path.join(shots, `08-report-${scheme}-${vp.width}.png`), fullPage: true });
    await p.context().close();
  }
});
await step("never touches the network and throws no errors", async () => {
  expect(external.length === 0, `external requests: ${external.slice(0, 3).join(", ")}`);
  expect(errors.length === 0, errors.slice(0, 3).join(" | "));
});

await browser.close();
console.log(failures ? `\n${failures} step(s) FAILED` : "\nAll preview steps passed");
process.exit(failures ? 1 : 0);
