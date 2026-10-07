package sa.ncai.agent.gateway.auth;

import com.fasterxml.jackson.databind.JsonNode;
import java.util.ArrayList;
import java.util.List;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import sa.ncai.agent.gateway.GatewayProperties;

/**
 * The Keycloak Admin REST API (README §11), reached with the service account of the confidential
 * {@code gateway} client (client credentials). Today it only lists the realm's {@code policy:*}
 * roles for the admin policy form (ADR-0107).
 */
@Component
public class KeycloakAdminClient {

    /** Keycloak could not be reached, or refused the service account. */
    public static class IdentityUnavailableException extends RuntimeException {
        public IdentityUnavailableException(String message, Throwable cause) {
            super(message, cause);
        }
    }

    /** Roles per Admin API page. */
    public static final int PAGE = 100;

    private final GatewayProperties.Admin admin;
    private final RestClient client;

    public KeycloakAdminClient(RestClient.Builder builder, GatewayProperties properties) {
        this.admin = properties.keycloak().admin();
        this.client = builder.build();
    }

    /** The realm roles named {@code policy:*}, sorted. */
    public List<String> policyRoles() {
        if (admin == null || admin.baseUrl() == null || admin.clientSecret() == null || admin.clientSecret().isBlank()) {
            throw new IdentityUnavailableException("the Keycloak Admin API is not configured", null);
        }
        try {
            MultiValueMap<String, String> form = new LinkedMultiValueMap<>();
            form.add("grant_type", "client_credentials");
            form.add("client_id", admin.clientId());
            form.add("client_secret", admin.clientSecret());
            JsonNode token = client.post()
                    .uri(admin.baseUrl() + "/realms/{realm}/protocol/openid-connect/token", admin.realm())
                    .contentType(MediaType.APPLICATION_FORM_URLENCODED)
                    .body(form)
                    .retrieve()
                    .body(JsonNode.class);
            String bearer = "Bearer " + token.path("access_token").asText();
            List<String> names = new ArrayList<>();
            // Page through every match: a realm may hold more policy roles than one page.
            for (int first = 0; ; first += PAGE) {
                JsonNode roles = client.get()
                        .uri(admin.baseUrl() + "/admin/realms/{realm}/roles?search={search}"
                                        + "&briefRepresentation=true&first={first}&max={max}",
                                admin.realm(), GatewayPrincipal.POLICY_ROLE_PREFIX, first, PAGE)
                        .header("Authorization", bearer)
                        .retrieve()
                        .body(JsonNode.class);
                if (roles == null || roles.isEmpty()) {
                    break;
                }
                roles.forEach(role -> {
                    String name = role.path("name").asText("");
                    // The same rule the gateway forwards by, so the form never offers a role that selects nothing.
                    if (GatewayPrincipal.isPolicyRole(name)) {
                        names.add(name);
                    }
                });
                if (roles.size() < PAGE) {
                    break;
                }
            }
            names.sort(null);
            return names.stream().distinct().toList();
        } catch (RestClientException | NullPointerException e) {
            throw new IdentityUnavailableException("Keycloak Admin API: " + e.getMessage(), e);
        }
    }
}
