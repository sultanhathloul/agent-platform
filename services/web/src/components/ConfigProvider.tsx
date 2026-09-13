"use client";

import { createContext, useContext, useMemo, type ReactNode } from "react";
import type { RuntimeConfig } from "@/lib/runtime-config";

const ConfigContext = createContext<RuntimeConfig | null>(null);

export function ConfigProvider({ config, children }: { config: RuntimeConfig; children: ReactNode }) {
  const { keycloakUrl, realm, clientId, gatewayUrl } = config;
  // Stable identity across router.refresh(), so auth is not re-initialized.
  const value = useMemo(
    () => ({ keycloakUrl, realm, clientId, gatewayUrl }),
    [keycloakUrl, realm, clientId, gatewayUrl],
  );
  return <ConfigContext.Provider value={value}>{children}</ConfigContext.Provider>;
}

export function useRuntimeConfig(): RuntimeConfig {
  const value = useContext(ConfigContext);
  if (!value) throw new Error("useRuntimeConfig must be used inside ConfigProvider");
  return value;
}
