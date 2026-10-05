import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, MemoryRouter } from "react-router-dom";
import App from "./App";
import "./styles.css";

async function boot() {
  // The static preview (`npm run build:demo`) has no server: an in-browser backend replays recorded runs instead.
  // The guard is written inline so the normal build drops this whole branch, fixtures loader included.
  const demo = import.meta.env.VITE_DEMO === "1";
  if (demo) {
    const { installDemoBackend } = await import("./demo/backend");
    await installDemoBackend();
  }
  const Router = demo ? MemoryRouter : BrowserRouter; // a preview inside an iframe can't rely on the address bar
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <Router>
        <App />
      </Router>
    </StrictMode>,
  );
}

boot().catch((e) => {
  const root = document.getElementById("root");
  if (root) root.textContent = `Could not start the preview: ${(e as Error).message}`;
});
