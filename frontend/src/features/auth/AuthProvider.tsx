import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import { AuthContext, type AuthContextValue, type AuthStatus } from "@/features/auth/authContext";
import { useApiClient } from "@/hooks/useApiClient";
import * as authApi from "@/services/authService";
import type { AuthSession } from "@/services/authSession";
import type { CurrentUser } from "@/types/auth";

interface AuthState {
  status: AuthStatus;
  user: CurrentUser | null;
  sessionExpired: boolean;
}

/**
 * Owns the signed-in state for the React tree.
 *
 * On start it restores the session from the refresh cookie (one refresh call).
 * On any sign-out (deliberate, expired, or from another tab) it clears every
 * cached query so no user data outlives the session.
 */
export function AuthProvider({ session, children }: { session: AuthSession; children: ReactNode }) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  const [state, setState] = useState<AuthState>(() =>
    session.currentUser
      ? { status: "authenticated", user: session.currentUser, sessionExpired: false }
      : { status: "loading", user: null, sessionExpired: false },
  );

  useEffect(
    () =>
      session.subscribe((event) => {
        if (event.type === "signed-in") {
          setState({ status: "authenticated", user: event.user, sessionExpired: false });
        } else {
          queryClient.clear();
          setState({ status: "unauthenticated", user: null, sessionExpired: event.reason === "expired" });
        }
      }),
    [session, queryClient],
  );

  useEffect(() => {
    if (session.isAuthenticated) return;
    let cancelled = false;
    void session.refresh().then((restored) => {
      if (!restored && !cancelled) {
        setState((current) =>
          current.status === "loading" ? { status: "unauthenticated", user: null, sessionExpired: false } : current,
        );
      }
    });
    return () => {
      cancelled = true;
    };
  }, [session]);

  const login = useCallback(
    async (email: string, password: string) => {
      queryClient.clear();
      session.establish(await authApi.login(client, email, password));
    },
    [client, session, queryClient],
  );

  const logout = useCallback(() => session.signOut(), [session]);

  const logoutEverywhere = useCallback(async () => {
    await authApi.logoutAll(client);
    session.end("logout");
  }, [client, session]);

  const changePassword = useCallback(
    async (currentPassword: string, newPassword: string) => {
      session.establish(await authApi.changePassword(client, currentPassword, newPassword));
    },
    [client, session],
  );

  const value = useMemo<AuthContextValue>(
    () => ({
      ...state,
      login,
      logout,
      logoutEverywhere,
      changePassword,
      hasPermission: (permission) => state.user?.permissions.includes(permission) ?? false,
    }),
    [state, login, logout, logoutEverywhere, changePassword],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
