"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { getMe } from "@/lib/gateway";
import { useAuth } from "./AuthProvider";
import { useRuntimeConfig } from "./ConfigProvider";

/**
 * Whether the signed-in user is an administrator, read once per session from the gateway's /v1/me, for
 * what the UI shows (the admin links, admin-only chips, ADR-0416). The gateway checks the role on every
 * admin call; this decides display only.
 */
const AdminContext = createContext(false);

export function MeProvider({ children }: { children: ReactNode }) {
  const auth = useAuth();
  const { gatewayUrl } = useRuntimeConfig();
  const signedIn = auth.status === "authenticated";
  const { getAccessToken } = auth;
  const [admin, setAdmin] = useState(false);

  useEffect(() => {
    if (!signedIn) {
      setAdmin(false);
      return;
    }
    let active = true;
    void (async () => {
      const token = await getAccessToken();
      if (!token) return;
      const me = await getMe({ gatewayUrl, token }).catch(() => null);
      if (active) setAdmin(Boolean(me?.is_admin));
    })();
    return () => {
      active = false;
    };
  }, [signedIn, getAccessToken, gatewayUrl]);

  return <AdminContext.Provider value={admin}>{children}</AdminContext.Provider>;
}

export function useIsAdmin(): boolean {
  return useContext(AdminContext);
}
