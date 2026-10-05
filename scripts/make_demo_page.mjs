#!/usr/bin/env node
/**
 * Folds the demo build (frontend/dist-demo) and the recorded fixtures into single, self-contained files:
 *
 *   docs/coherence-lab-preview.html    open it with a double-click: no server, no network, no install (committed)
 *   share/preview-fragment.html        the same page without <html>/<head> wrappers, for hosts that add their own (not committed)
 *
 * Run through `npm run build:demo` (from frontend/) or `make preview`.
 */
import { readFileSync, writeFileSync, mkdirSync, existsSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const dist = join(root, "frontend", "dist-demo");
const fixturePath = join(root, "frontend", "src", "demo", "fixtures.json.gz");
const docsDir = join(root, "docs");
const shareDir = join(root, "share");

for (const p of [join(dist, "index.html"), fixturePath]) {
  if (!existsSync(p)) {
    console.error(`Missing ${p}. Run \`npm run build:demo\` in frontend/ (and scripts/build_demo_fixtures.py first if the fixtures are absent).`);
    process.exit(1);
  }
}

const html = readFileSync(join(dist, "index.html"), "utf8");
const grab = (re, what) => {
  const m = html.match(re);
  if (!m) throw new Error(`Could not find the ${what} in dist-demo/index.html`);
  return readFileSync(join(dist, m[1]), "utf8");
};
const js = grab(/<script[^>]*\ssrc="\.\/(assets\/[^"]+\.js)"/, "script");
const css = grab(/<link[^>]*\shref="\.\/(assets\/[^"]+\.css)"/, "stylesheet");
const icon = html.match(/<link rel="icon" href="([^"]+)"/)?.[1] ?? "";
const fixtures = readFileSync(fixturePath).toString("base64");

// An inline <script> ends at the first "</script"; neutralise any occurrence inside the bundle.
const safeJs = js.replace(/<\/script/gi, "<\\/script");
const safeCss = css.replace(/<\/style/gi, "<\\/style");

const body = [
  `<div id="root"></div>`,
  `<script id="demo-fixtures" type="text/plain">${fixtures}</script>`,
  `<script type="module">${safeJs}</script>`,
].join("\n");

const standalone = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<title>Coherence Lab</title>
${icon ? `<link rel="icon" href="${icon}">` : ""}
<style>${safeCss}</style>
</head>
<body>
${body}
</body>
</html>
`;

const fragment = `<title>Coherence Lab</title>
<style>${safeCss}</style>
${body}
`;

mkdirSync(docsDir, { recursive: true });
mkdirSync(shareDir, { recursive: true });
writeFileSync(join(docsDir, "coherence-lab-preview.html"), standalone);
writeFileSync(join(shareDir, "preview-fragment.html"), fragment);
const mb = (p) => (statSync(p).size / 1e6).toFixed(2);
console.log(`docs/coherence-lab-preview.html  ${mb(join(docsDir, "coherence-lab-preview.html"))} MB`);
console.log(`share/preview-fragment.html      ${mb(join(shareDir, "preview-fragment.html"))} MB`);
