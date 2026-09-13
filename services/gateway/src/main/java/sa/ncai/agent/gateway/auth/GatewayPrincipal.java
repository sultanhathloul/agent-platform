package sa.ncai.agent.gateway.auth;

import java.util.List;
import java.util.Set;
import java.util.regex.Pattern;
import org.springframework.security.oauth2.jwt.Jwt;

/**
 * Principal context sent to the agent (README §6.1.2, §11). Only {@code cap:*} and
 * {@code kb:*:read} roles become permissions (ADR-0006); tenant defaults to "default" (ADR-0010).
 * {@code roles} are the remaining application realm roles; the agent uses them only to pick
 * model tier ceilings, never to authorize (ADR-0040).
 */
public record GatewayPrincipal(
        String userId,
        String tenantId,
        List<String> permissions,
        List<String> roles,
        String displayName,
        boolean admin) {

    public static final String DEFAULT_TENANT = "default";
    public static final String ADMIN_ROLE = "admin";

    private static final Pattern PERMISSION = Pattern.compile("cap:[a-z0-9_]+|kb:[a-z0-9_-]+:read");
    private static final Pattern KEYCLOAK_BUILT_IN = Pattern.compile("default-roles-.*|offline_access|uma_authorization");

    public static GatewayPrincipal from(Jwt jwt) {
        Set<String> realmRoles = KeycloakRoles.realmRoles(jwt);
        List<String> permissions = realmRoles.stream()
                .filter(role -> PERMISSION.matcher(role).matches())
                .distinct()
                .sorted()
                .toList();
        List<String> roles = realmRoles.stream()
                .filter(role -> !role.contains(":") && !KEYCLOAK_BUILT_IN.matcher(role).matches())
                .distinct()
                .sorted()
                .toList();
        return new GatewayPrincipal(
                jwt.getSubject(), tenant(jwt), permissions, roles, displayName(jwt), realmRoles.contains(ADMIN_ROLE));
    }

    private static String tenant(Jwt jwt) {
        String tenant = jwt.getClaimAsString("tenant_id");
        return tenant == null || tenant.isBlank() ? DEFAULT_TENANT : tenant.strip();
    }

    private static String displayName(Jwt jwt) {
        String name = jwt.getClaimAsString("name");
        return name != null && !name.isBlank() ? name : jwt.getClaimAsString("preferred_username");
    }
}
