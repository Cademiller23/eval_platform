import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `vite build --mode demo` produces the static preview (dist-demo/): one JS and one CSS file with the font inlined,
// which scripts/make_demo_page.mjs then folds into a single self-contained HTML file.
export default defineConfig(({ mode }) => {
  const demo = mode === "demo";
  return {
    plugins: [react()],
    base: demo ? "./" : "/",
    server: {
      port: 5173,
      proxy: { "/api": { target: "http://localhost:8000", changeOrigin: true } },
    },
    build: {
      outDir: demo ? "dist-demo" : "dist",
      sourcemap: false,
      assetsInlineLimit: demo ? 1_000_000 : 4096,
      cssCodeSplit: !demo,
      rollupOptions: demo ? { output: { inlineDynamicImports: true } } : {},
    },
  };
});
