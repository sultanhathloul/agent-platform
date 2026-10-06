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
            JsonNode roles = client.get()
                    .uri(admin.baseUrl() + "/admin/realms/{realm}/roles?search={search}&briefRepresentation=true&max=500",
                            admin.realm(), GatewayPrincipal.POLICY_ROLE_PREFIX)
                    .header("Authorization", "Bearer " + token.path("access_token").asText())
                    .retrieve()
                    .body(JsonNode.class);
            List<String> names = new ArrayList<>();
            if (roles != null) {
                roles.forEach(role -> {
                    String name = role.path("name").asText("");
                    if (name.startsWith(GatewayPrincipal.POLICY_ROLE_PREFIX)) {
                        names.add(name);
                    }
                });
            }
            names.sort(null);
            return names;
        } catch (RestClientException | NullPointerException e) {
            throw new IdentityUnavailableException("Keycloak Admin API: " + e.getMessage(), e);
        }
    }
}
