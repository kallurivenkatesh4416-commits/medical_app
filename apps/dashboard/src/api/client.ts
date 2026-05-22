/**
 * Slice 15 — API client + in-memory access token + sessionStorage refresh.
 *
 * Auth storage strategy (Slice 15 scoping decision, Path 1):
 *
 * - Access token: held only in a module-level closure variable. Never
 *   touches `localStorage`. Lost on tab close — by design.
 * - Refresh token: `sessionStorage` (per-tab, cleared on tab close).
 *   Slice 2's refresh-token rotation + reuse detection limits the
 *   blast radius if the refresh token leaks via XSS — a stolen
 *   refresh that the attacker uses invalidates the whole family the
 *   next time the doctor refreshes.
 *
 * Silent re-auth on reload: if `sessionStorage` holds a refresh token
 * on app boot, the client POSTs `/auth/refresh` and the access token
 * comes back in memory. The doctor stays signed in across browser
 * refreshes; closing the tab logs them out.
 *
 * `/auth/refresh` 401 → memory + sessionStorage cleared → app routes
 * back to `/login`. The `sessionExpired` event lets AuthContext show
 * a toast when this happens mid-session.
 */

const REFRESH_KEY = "auth.refresh_token.v1";

export type ErrorEnvelope = {
  code: string;
  message: string;
  details?: unknown;
};

export class ApiError extends Error {
  constructor(
    public status: number,
    public envelope: ErrorEnvelope | null,
    message?: string,
  ) {
    super(message ?? envelope?.message ?? `HTTP ${status}`);
    this.name = "ApiError";
  }
}

type RequestOptions = {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
  headers?: Record<string, string>;
  /** Skip the auto-refresh retry — used by /auth/refresh itself. */
  noRefresh?: boolean;
  /** Slice 4/14 idempotency-key for write endpoints that require it. */
  idempotencyKey?: string;
};

export type SessionExpiredListener = () => void;

/**
 * Single source of truth for tokens. Built as a class so tests can
 * spin up an isolated instance per test, the AuthContext can stash one
 * in a Provider, and a future cookie-auth migration replaces THIS file
 * without touching call sites.
 */
export class ApiClient {
  private accessToken: string | null = null;
  private listeners = new Set<SessionExpiredListener>();
  /** Coalesce concurrent refreshes: many parallel 401s should fire one /refresh. */
  private inflightRefresh: Promise<boolean> | null = null;

  constructor(
    public readonly baseUrl: string,
    private readonly storage: Storage = (globalThis as { sessionStorage?: Storage })
      .sessionStorage ?? new InMemoryStorage(),
    private readonly fetcher: typeof fetch = (...args) => fetch(...args),
  ) {}

  // --------------------------------------------------------------------- //
  // Token plumbing                                                        //
  // --------------------------------------------------------------------- //

  setSession({ access, refresh }: { access: string; refresh: string }) {
    this.accessToken = access;
    this.storage.setItem(REFRESH_KEY, refresh);
  }

  clearSession() {
    this.accessToken = null;
    this.storage.removeItem(REFRESH_KEY);
  }

  /** True only when at least the refresh token survived a reload. */
  hasStoredRefresh(): boolean {
    return Boolean(this.storage.getItem(REFRESH_KEY));
  }

  /** AuthContext subscribes to surface a toast when refresh fails. */
  onSessionExpired(listener: SessionExpiredListener) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private emitSessionExpired() {
    this.listeners.forEach((listener) => listener());
  }

  // --------------------------------------------------------------------- //
  // Public verbs                                                          //
  // --------------------------------------------------------------------- //

  get<T>(path: string, opts?: Omit<RequestOptions, "method" | "body">) {
    return this.request<T>(path, { ...opts, method: "GET" });
  }

  post<T>(path: string, body?: unknown, opts?: Omit<RequestOptions, "method" | "body">) {
    return this.request<T>(path, { ...opts, method: "POST", body });
  }

  patch<T>(path: string, body?: unknown, opts?: Omit<RequestOptions, "method" | "body">) {
    return this.request<T>(path, { ...opts, method: "PATCH", body });
  }

  /** Returns the parsed response. Throws `ApiError` on non-2xx. */
  async request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
    const resp = await this.requestRaw(path, opts);
    return this.parseOrThrow<T>(resp);
  }

  /**
   * Like `request` but returns the raw `Response` so the caller can
   * read a binary body (PDF, CSV). Still rides the auto-refresh path
   * on 401 and emits `sessionExpired` on a hard refresh failure.
   * Throws `ApiError` on non-2xx after the (one) retry attempt.
   */
  async requestRaw(path: string, opts: RequestOptions = {}): Promise<Response> {
    const first = await this.attempt(path, opts);
    if (first.status !== 401 || opts.noRefresh) {
      if (!first.ok) {
        const envelope = await this.tryReadEnvelope(first);
        throw new ApiError(first.status, envelope);
      }
      return first;
    }
    const refreshed = await this.refreshOnce();
    if (!refreshed) {
      this.emitSessionExpired();
      const envelope = await this.tryReadEnvelope(first);
      throw new ApiError(first.status, envelope);
    }
    const second = await this.attempt(path, opts);
    if (!second.ok) {
      const envelope = await this.tryReadEnvelope(second);
      throw new ApiError(second.status, envelope);
    }
    return second;
  }

  private async tryReadEnvelope(resp: Response): Promise<ErrorEnvelope | null> {
    try {
      const raw = await resp.clone().text();
      if (!raw) return null;
      const parsed = JSON.parse(raw) as { error?: ErrorEnvelope };
      return parsed.error ?? null;
    } catch {
      return null;
    }
  }

  /**
   * Boot-time silent refresh. Returns true iff the stored refresh
   * token was usable and a new access token is now in memory. A false
   * here is the AuthContext's signal to route to `/login`.
   */
  async tryRestoreSession(): Promise<boolean> {
    if (!this.hasStoredRefresh()) return false;
    return this.refreshOnce();
  }

  // --------------------------------------------------------------------- //
  // Internals                                                             //
  // --------------------------------------------------------------------- //

  private async attempt(path: string, opts: RequestOptions): Promise<Response> {
    const headers: Record<string, string> = {
      Accept: "application/json",
      ...opts.headers,
    };
    let body: BodyInit | undefined;
    if (opts.body !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(opts.body);
    }
    if (this.accessToken) headers.Authorization = `Bearer ${this.accessToken}`;
    if (opts.idempotencyKey) headers["Idempotency-Key"] = opts.idempotencyKey;
    return this.fetcher(`${this.baseUrl}${path}`, {
      method: opts.method ?? "GET",
      headers,
      body,
    });
  }

  private async parseOrThrow<T>(resp: Response): Promise<T> {
    const raw = await resp.text();
    let parsed: unknown = null;
    if (raw) {
      try {
        parsed = JSON.parse(raw);
      } catch {
        // Some endpoints (PDF download) return binary; callers that
        // need raw bytes should fetch directly with the access token.
      }
    }
    if (resp.ok) return parsed as T;
    const envelope =
      (parsed as { error?: ErrorEnvelope } | null)?.error ?? null;
    throw new ApiError(resp.status, envelope);
  }

  private async refreshOnce(): Promise<boolean> {
    if (this.inflightRefresh) return this.inflightRefresh;
    this.inflightRefresh = (async () => {
      const raw = this.storage.getItem(REFRESH_KEY);
      if (!raw) return false;
      try {
        const resp = await this.attempt("/api/v1/auth/refresh", {
          method: "POST",
          body: { refresh_token: raw },
          noRefresh: true,
        });
        if (!resp.ok) {
          this.clearSession();
          return false;
        }
        const json = (await resp.json()) as {
          access_token: string;
          refresh_token: string;
        };
        this.setSession({ access: json.access_token, refresh: json.refresh_token });
        return true;
      } catch {
        this.clearSession();
        return false;
      } finally {
        // The coalesced promise must reset BEFORE any awaiter resumes,
        // so a subsequent 401 within the same tick triggers a fresh
        // refresh — not the resolved one from this round.
        queueMicrotask(() => {
          this.inflightRefresh = null;
        });
      }
    })();
    return this.inflightRefresh;
  }
}

/** Fallback storage for environments without `sessionStorage` (SSR / tests). */
class InMemoryStorage implements Storage {
  private map = new Map<string, string>();
  get length() {
    return this.map.size;
  }
  clear() {
    this.map.clear();
  }
  getItem(key: string) {
    return this.map.get(key) ?? null;
  }
  key(index: number) {
    return Array.from(this.map.keys())[index] ?? null;
  }
  removeItem(key: string) {
    this.map.delete(key);
  }
  setItem(key: string, value: string) {
    this.map.set(key, value);
  }
}

export { InMemoryStorage };

/** Idempotency key shaped like a UUIDv4 — opaque to the backend. */
export function newIdempotencyKey(): string {
  const cryptoRef = (globalThis as { crypto?: Crypto }).crypto;
  if (cryptoRef?.randomUUID) return cryptoRef.randomUUID();
  // Older browser fallback — `crypto.getRandomValues` is widely
  // supported in every target environment we care about.
  const bytes = new Uint8Array(16);
  cryptoRef?.getRandomValues?.(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}
