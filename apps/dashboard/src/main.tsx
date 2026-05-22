/**
 * Slice 15 — production entry point.
 *
 * Owns the singletons: one `ApiClient`, one `QueryClient`. Tests
 * bypass this file and render `<AppRoot>` (or a smaller subtree)
 * directly with injected fakes.
 */

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";

import App from "./App";
import { ApiClient } from "./api/client";
import { AuthProvider } from "./auth/AuthContext";
import "./index.css";

const apiBaseUrl =
  import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

const apiClient = new ApiClient(apiBaseUrl);

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Stale-while-revalidate by default. Live polling is opted into
      // per-query via `refetchInterval` (alerts list, case detail).
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("Root element #root not found");

createRoot(rootEl).render(
  <StrictMode>
    <BrowserRouter>
      <QueryClientProvider client={queryClient}>
        <AuthProvider client={apiClient}>
          <App />
        </AuthProvider>
      </QueryClientProvider>
    </BrowserRouter>
  </StrictMode>,
);
