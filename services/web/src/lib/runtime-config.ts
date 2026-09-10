// Browser-facing URLs, read from the server environment on every request so a
// single image serves any deployment (nothing is baked in at build time).
export type RuntimeConfig = {
  keycloakUrl: string;
  realm: string;
  clientId: string;
  gatewayUrl: string;
};

const trimSlash = (url: string) => url.replace(/\/+$/, "");

export function readRuntimeConfig(env: NodeJS.ProcessEnv = process.env): RuntimeConfig {
  return {
    keycloakUrl: trimSlash(env.KEYCLOAK_PUBLIC_URL || "http://localhost:8081"),
    realm: env.KC_REALM || "agent",
    clientId: env.KC_WEB_CLIENT_ID || "web",
    gatewayUrl: trimSlash(env.GATEWAY_PUBLIC_URL || "http://localhost:8080"),
  };
}
