import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { App } from "./App";
import { WEB_BASE } from "./api/client";
import { AuthProvider } from "./auth/AuthProvider";
import "./styles/global.css";

const root = document.getElementById("root");
if (!root) {
  throw new Error("Application root is missing");
}

createRoot(root).render(
  <StrictMode>
    <BrowserRouter basename={WEB_BASE}>
      <AuthProvider>
        <App />
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
);
