package sa.ncai.agent.gateway.auth;

import java.util.Collection;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import org.springframework.security.oauth2.jwt.Jwt;

/** Reads Keycloak realm roles from {@code realm_access.roles}. The gateway is the only reader of roles. */
public final class KeycloakRoles {

    private KeycloakRoles() {}

    public static Set<String> realmRoles(Jwt jwt) {
        Set<String> roles = new TreeSet<>();
        if (jwt.getClaims().get("realm_access") instanceof Map<?, ?> realmAccess
                && realmAccess.get("roles") instanceof Collection<?> values) {
            for (Object value : values) {
                if (value instanceof String role) {
                    roles.add(role);
                }
            }
        }
        return roles;
    }
}
