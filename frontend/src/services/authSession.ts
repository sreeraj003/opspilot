import { ApiError, type ApiClient } from "@/services/apiClient";
import { logout as logoutRequest, refreshSession } from "@/services/authService";
import type { CurrentUser, TokenResponse } from "@/types/auth";

export type SessionEndReason = "logout" | "expired";

export type SessionEvent =
  | { type: "signed-in"; user: CurrentUser }
  | { type: "signed-out"; reason: SessionEndReason };

type Listener = (event: SessionEvent) => void;

const CONCURRENT_REFRESH_RETRY_MS = 250;
const BROADCAST_CHANNEL = "opspilot-auth";

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Holds the signed-in state for this tab.
 *
 * - The access token lives only in this object's memory: never in
 *   localStorage, sessionStorage or a readable cookie, and never logged.
 * - The refresh token is an HttpOnly cookie that scripts cannot read; the
 *   browser sends it to /api/v1/auth only.
 * - Refreshes are de-duplicated: concurrent 401s, React StrictMode double
 *   effects and the initial session restore all share one in-flight refresh,
 *   so the refresh token is rotated once. A 409 (another tab rotated it a
 *   moment ago) is retried once with the cookie that tab received.
 * - A failed refresh ends the session; requests are retried at most once, so
 *   there are no refresh loops.
 */
export class AuthSession {
  private accessToken: string | null = null;
  private user: CurrentUser | null = null;
  private refreshing: Promise<boolean> | null = null;
  private readonly listeners = new Set<Listener>();
  private readonly channel: BroadcastChannel | null;

  constructor(
    private readonly client: ApiClient,
    options: { broadcast?: boolean } = {},
  ) {
    client.useAuthentication(
      (): Record<string, string> => (this.accessToken ? { Authorization: `Bearer ${this.accessToken}` } : {}),
      () => this.refresh(),
    );
    const broadcast = options.broadcast ?? typeof BroadcastChannel !== "undefined";
    this.channel = broadcast ? new BroadcastChannel(BROADCAST_CHANNEL) : null;
    this.channel?.addEventListener("message", (event: MessageEvent<unknown>) => {
      // Another tab signed out: the shared cookie session is gone, so end ours too.
      if (event.data === "signed-out" && this.user) this.end("logout", false);
    });
  }

  get currentUser(): CurrentUser | null {
    return this.user;
  }

  get isAuthenticated(): boolean {
    return this.accessToken !== null;
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  /** Adopt a token response from login, refresh or password change. */
  establish(response: TokenResponse): void {
    this.accessToken = response.access_token;
    this.user = response.user;
    this.emit({ type: "signed-in", user: response.user });
  }

  /** Obtain a new access token with the refresh cookie. Resolves whether it worked. */
  refresh(): Promise<boolean> {
    this.refreshing ??= this.doRefresh().finally(() => {
      this.refreshing = null;
    });
    return this.refreshing;
  }

  async signOut(): Promise<void> {
    try {
      await logoutRequest(this.client);
    } catch {
      // The local session ends regardless; the server session expires on its own.
    } finally {
      this.end("logout", true);
    }
  }

  /** End the local session without contacting the server (e.g. after logout-all). */
  end(reason: SessionEndReason, broadcast = true): void {
    const wasSignedIn = this.user !== null || this.accessToken !== null;
    this.accessToken = null;
    this.user = null;
    if (broadcast) this.channel?.postMessage("signed-out");
    if (wasSignedIn || reason === "logout") this.emit({ type: "signed-out", reason });
  }

  private async doRefresh(): Promise<boolean> {
    for (let attempt = 0; attempt < 2; attempt += 1) {
      try {
        this.establish(await refreshSession(this.client));
        return true;
      } catch (error) {
        if (error instanceof ApiError && error.status === 409 && attempt === 0) {
          await wait(CONCURRENT_REFRESH_RETRY_MS);
          continue;
        }
        if (error instanceof ApiError && (error.status === 401 || error.status === 403 || error.status === 409)) {
          this.end("expired", false);
        }
        // Network errors and 5xx: keep the state; the caller's request fails normally.
        return false;
      }
    }
    return false;
  }

  private emit(event: SessionEvent): void {
    for (const listener of this.listeners) listener(event);
  }
}
