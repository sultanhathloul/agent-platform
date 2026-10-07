package sa.ncai.agent.gateway.auth;

import java.util.List;
import java.util.Set;
import java.util.regex.Pattern;
import org.springframework.security.oauth2.jwt.Jwt;

/**
 * Principal context sent to the agent (README §6.1.2, §11). Only {@code cap:*} and
 * {@code kb:*:read} roles become permissions (ADR-0006); tenant defaults to "default" (ADR-0010).
 * Realm roles prefixed {@code policy:} become {@code policyRoles}: they select role-scoped guard
 * policies, grant nothing, and are the only roles that leave the gateway (ADR-0107).
 */
public record GatewayPrincipal(
        String userId,
        String tenantId,
        List<String> permissions,
        String displayName,
        boolean admin,
        List<String> policyRoles) {

    public static final String DEFAULT_TENANT = "default";
    public static final String ADMIN_ROLE = "admin";
    public static final String POLICY_ROLE_PREFIX = "policy:";
    /** The policy roles the gateway forwards (ADR-0107); the admin form lists the same ones. */
    public static boolean isPolicyRole(String role) {
        return POLICY_ROLE.matcher(role).matches();
    }

    private static final Pattern POLICY_ROLE = Pattern.compile("policy:[a-z0-9][a-z0-9_-]{0,63}");

    private static final Pattern PERMISSION = Pattern.compile("cap:[a-z0-9_]+|kb:[a-z0-9_-]+:read");

    public static GatewayPrincipal from(Jwt jwt) {
        Set<String> roles = KeycloakRoles.realmRoles(jwt);
        List<String> permissions = roles.stream()
                .filter(role -> PERMISSION.matcher(role).matches())
                .distinct()
                .sorted()
                .toList();
        List<String> policyRoles = roles.stream()
                .filter(GatewayPrincipal::isPolicyRole)
                .distinct()
                .sorted()
                .toList();
        return new GatewayPrincipal(jwt.getSubject(), tenant(jwt), permissions, displayName(jwt),
                roles.contains(ADMIN_ROLE), policyRoles);
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
