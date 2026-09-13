"use client";

import type { User, UserManager } from "oidc-client-ts";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { createUserManager } from "@/lib/auth";
import { safeReturnPath } from "@/lib/return-path";
import { useRuntimeConfig } from "./ConfigProvider";

type Status = "loading" | "anonymous" | "authenticated";

type AuthContextValue = {
  status: Status;
  /** True once the UserManager exists in the browser. */
  ready: boolean;
  displayName: string;
  login: (returnTo?: string) => Promise<void>;
  logout: () => Promise<void>;
  getAccessToken: () => Promise<string | null>;
  /** Completes the redirect callback and returns the path to go back to. */
  completeSignin: () => Promise<string>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

// React strict mode runs effects twice; the authorization code may be redeemed only once.
let pendingCallback: Promise<string> | null = null;

export function AuthProvider({ children }: { children: ReactNode }) {
  const config = useRuntimeConfig();
  const [manager, setManager] = useState<UserManager | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [status, setStatus] = useState<Status>("loading");

  useEffect(() => {
    const um = createUserManager(config);
    let active = true;
    const onLoaded = (u: User) => {
      if (!active) return;
      setUser(u);
      setStatus("authenticated");
    };
    const onUnloaded = () => {
      if (!active) return;
      setUser(null);
      setStatus("anonymous");
    };
    const onRenewError = () => void um.removeUser();
    um.events.addUserLoaded(onLoaded);
    um.events.addUserUnloaded(onUnloaded);
    um.events.addSilentRenewError(onRenewError);
    setManager(um);

    void (async () => {
      let stored = await um.getUser();
      if (stored?.expired) stored = await um.signinSilent().catch(() => null);
      if (!active) return;
      if (stored && !stored.expired) onLoaded(stored);
      else setStatus((s) => (s === "loading" ? "anonymous" : s));
    })();

    return () => {
      active = false;
      um.events.removeUserLoaded(onLoaded);
      um.events.removeUserUnloaded(onUnloaded);
      um.events.removeSilentRenewError(onRenewError);
      um.stopSilentRenew();
    };
  }, [config]);

  const login = useCallback(
    async (returnTo?: string) => {
      if (!manager) return;
      await manager.signinRedirect({ state: { returnTo: safeReturnPath(returnTo ?? window.location.pathname) } });
    },
    [manager],
  );

  const logout = useCallback(async () => {
    if (!manager) return;
    await manager.signoutRedirect();
  }, [manager]);

  const getAccessToken = useCallback(async () => {
    if (!manager) return null;
    const current = await manager.getUser();
    if (!current) return null;
    if (!current.expired) return current.access_token;
    const renewed = await manager.signinSilent().catch(() => null);
    return renewed?.access_token ?? null;
  }, [manager]);

  const completeSignin = useCallback(() => {
    if (!manager) return Promise.reject(new Error("auth is not ready"));
    pendingCallback ??= manager
      .signinRedirectCallback()
      .then((u) => safeReturnPath((u.state as { returnTo?: unknown } | undefined)?.returnTo))
      .catch((error: unknown) => {
        pendingCallback = null;
        throw error;
      });
    return pendingCallback;
  }, [manager]);

  const displayName = user?.profile.name ?? user?.profile.preferred_username ?? "";

  const value = useMemo<AuthContextValue>(
    () => ({ status, ready: manager !== null, displayName, login, logout, getAccessToken, completeSignin }),
    [status, manager, displayName, login, logout, getAccessToken, completeSignin],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
