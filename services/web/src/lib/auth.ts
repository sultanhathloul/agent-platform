import { UserManager, WebStorageStateStore } from "oidc-client-ts";
import type { RuntimeConfig } from "./runtime-config";

// Keycloak public client `web`: authorization code + PKCE (S256, the
// oidc-client-ts default). Tokens stay in sessionStorage for this tab only;
// renewal uses the refresh token, so no hidden iframe is needed.
export function createUserManager(config: RuntimeConfig): UserManager {
  const origin = window.location.origin;
  return new UserManager({
    authority: `${config.keycloakUrl}/realms/${encodeURIComponent(config.realm)}`,
    client_id: config.clientId,
    redirect_uri: `${origin}/auth/callback`,
    post_logout_redirect_uri: `${origin}/`,
    response_type: "code",
    scope: "openid profile email",
    userStore: new WebStorageStateStore({ store: window.sessionStorage }),
    automaticSilentRenew: true,
    loadUserInfo: false,
  });
}
