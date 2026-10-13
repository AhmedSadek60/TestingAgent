import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { startBrowserErrorBuffer } from "./lib/browserErrors";
import "./styles.css";

startBrowserErrorBuffer(); // what this tab sees go wrong is kept in memory, so a report can include it if the person chooses

const root = document.getElementById("root");
if (!root) throw new Error("index.html has no #root element");
createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
