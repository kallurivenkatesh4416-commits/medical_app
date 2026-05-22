/**
 * Slice 18b — dashboard cookie-session API client.
 *
 * The backend owns the HttpOnly access and refresh cookies. Browser JS keeps
 * only the signed CSRF token returned by `/auth/csrf`, includes credentials on
 * every API fetch, and retries one 401 through cookie refresh rotation.
 */

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
  /** Skip the auto-refresh retry — used by auth bootstrap internals. */
  noRefresh?: boolean;
  /** Slice 4/14 idempotency-key for write endpoints that require it. */
  idempotencyKey?: string;
};

export type SessionExpiredListener = () => void;

/**
 * Single source of truth for cookie-session transport and the in-memory CSRF
 * token. Tests spin up an isolated instance per fake backend.
 */
export class ApiClient {
  private csrfToken: string | null = null;
  private listeners = new Set<SessionExpiredListener>();
  /** Coalesce concurrent refreshes: many parallel 401s should fire one /refresh. */
  private inflightRefresh: Promise<boolean> | null = null;

  constructor(
    public readonly baseUrl: string,
    private readonly fetcher: typeof fetch = (...args) => fetch(...args),
  ) {}

  clearSession() {
    this.csrfToken = null;
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
    await this.ensureCsrfForWrite(path, opts);
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
   * Boot-time cookie probe. A cookie session can have an expired access token
   * and a live refresh cookie, so bootstrap grabs CSRF first and lets `/me`
   * ride the normal refresh retry if needed.
   */
  async tryRestoreSession(): Promise<boolean> {
    return this.fetchCsrfToken();
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
    if (this.shouldAttachCsrf(opts) && this.csrfToken) {
      headers["X-CSRF-Token"] = this.csrfToken;
    }
    if (opts.idempotencyKey) headers["Idempotency-Key"] = opts.idempotencyKey;
    return this.fetcher(`${this.baseUrl}${path}`, {
      method: opts.method ?? "GET",
      headers,
      body,
      credentials: "include",
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
      if (!this.csrfToken && !(await this.fetchCsrfToken())) return false;
      try {
        const resp = await this.attempt("/api/v1/auth/refresh", {
          method: "POST",
          noRefresh: true,
        });
        if (!resp.ok) {
          this.clearSession();
          return false;
        }
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

  private async fetchCsrfToken(): Promise<boolean> {
    try {
      const resp = await this.attempt("/api/v1/auth/csrf", {
        method: "GET",
        noRefresh: true,
      });
      if (!resp.ok) {
        this.clearSession();
        return false;
      }
      const json = (await resp.json()) as { csrf_token?: string };
      if (!json.csrf_token) {
        this.clearSession();
        return false;
      }
      this.csrfToken = json.csrf_token;
      return true;
    } catch {
      this.clearSession();
      return false;
    }
  }

  private async ensureCsrfForWrite(path: string, opts: RequestOptions): Promise<void> {
    if (!this.shouldAttachCsrf(opts) || this.publicAuthWrite(path) || this.csrfToken) {
      return;
    }
    await this.fetchCsrfToken();
  }

  private shouldAttachCsrf(opts: RequestOptions): boolean {
    return ["POST", "PATCH", "DELETE"].includes(opts.method ?? "GET");
  }

  private publicAuthWrite(path: string): boolean {
    return path === "/api/v1/auth/otp/request" || path === "/api/v1/auth/otp/verify";
  }
}

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
