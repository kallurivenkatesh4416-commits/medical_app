/**
 * Slice 15 — Vitest+RTL helpers.
 *
 * The dashboard's HTTP is funnelled through a single `ApiClient`. Tests
 * build one with a custom `fetch` so we never touch the network — every
 * request gets routed through the test's response queue. This mirrors
 * the Flutter codebase's typedef-seam pattern: production wires
 * `globalThis.fetch`, tests pass an inline function.
 */

import { render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { ApiClient, InMemoryStorage } from "../api/client";
import { AuthProvider } from "../auth/AuthContext";
import App from "../App";

export type FakeResponse =
  | { status: number; json: unknown }
  | { status: number; text: string };

export type Route = {
  /** Substring match on `${method} ${path}` so tests can stay tolerant
   *  of query-string variations like `?days=7`. */
  match: string;
  respond: () => FakeResponse | Promise<FakeResponse>;
  /** Capture the request body for later assertion (optional). */
  capture?: (body: unknown) => void;
};

export function makeFetch(routes: Route[]): typeof fetch {
  return async (input, init) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = (init?.method ?? "GET").toUpperCase();
    const key = `${method} ${url}`;
    const route = routes.find((r) => key.includes(r.match));
    if (!route) {
      // Helpful failure rather than the cryptic "fetch is not a function"
      throw new Error(`Unmatched test request: ${key}`);
    }
    if (route.capture && init?.body) {
      try {
        route.capture(JSON.parse(init.body as string));
      } catch {
        route.capture(init.body);
      }
    }
    const resolved = await route.respond();
    const body = "json" in resolved ? JSON.stringify(resolved.json) : resolved.text;
    return new Response(body, {
      status: resolved.status,
      headers: { "Content-Type": "application/json" },
    });
  };
}

export function makeClient(routes: Route[]): ApiClient {
  return new ApiClient("http://api.test", new InMemoryStorage(), makeFetch(routes));
}

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false, staleTime: Infinity },
      mutations: { retry: false },
    },
  });
}

export function renderApp({
  routes,
  initialPath = "/",
  preAuth,
}: {
  routes: Route[];
  initialPath?: string;
  /** Pre-set session tokens so the bootstrap silent-refresh path
   *  completes before the first render. Tests that exercise the
   *  unauthenticated path leave this null. */
  preAuth?: { access: string; refresh: string } | null;
}) {
  const client = makeClient(routes);
  if (preAuth) client.setSession(preAuth);
  const qc = makeQueryClient();
  const result = render(
    <MemoryRouter initialEntries={[initialPath]}>
      <QueryClientProvider client={qc}>
        <AuthProvider client={client}>
          <App />
        </AuthProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  );
  return { ...result, client, qc };
}
