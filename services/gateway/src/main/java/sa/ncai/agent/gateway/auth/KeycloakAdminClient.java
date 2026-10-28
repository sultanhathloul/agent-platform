package sa.ncai.agent.gateway.auth;

import com.fasterxml.jackson.databind.JsonNode;
import java.net.http.HttpClient;
import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.http.MediaType;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.web.client.HttpClientErrorException;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;
import sa.ncai.agent.gateway.GatewayProperties;

/**
 * The Keycloak Admin REST API (README §11), reached with the service account of the confidential
 * {@code gateway} client (client credentials): it lists the realm's {@code policy:*} roles for the
 * admin policy form (ADR-0107) and creates a knowledge base's realm role {@code kb:<slug>:read}
 * when the KB is created (phase 08, I1).
 *
 * <p>Every call is bounded from request to the end of its body (CLAUDE.md rule 12c): connect within
 * {@link #CONNECT_BOUND}, the whole exchange within {@link #CALL_BOUND} (Spring's JDK request
 * factory applies its read timeout to the whole exchange, ADR-0256). A hung Keycloak answers the
 * admin {@code identity_unavailable} after at most two such calls (the token, then the role).
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
    /** Connecting to Keycloak. */
    static final Duration CONNECT_BOUND = Duration.ofSeconds(3);
    /** One Admin API call, request to the end of its body. */
    static final Duration CALL_BOUND = Duration.ofSeconds(8);

    private final GatewayProperties.Admin admin;
    private final RestClient client;

    public KeycloakAdminClient(RestClient.Builder builder, GatewayProperties properties) {
        this.admin = properties.keycloak().admin();
        HttpClient http = HttpClient.newBuilder()
                .version(HttpClient.Version.HTTP_1_1)
                .connectTimeout(CONNECT_BOUND)
                .build();
        JdkClientHttpRequestFactory factory = new JdkClientHttpRequestFactory(http);
        factory.setReadTimeout(CALL_BOUND);
        this.client = builder.clone().requestFactory(factory).build();
    }

    private void requireConfigured() {
        if (admin == null || admin.baseUrl() == null || admin.clientSecret() == null || admin.clientSecret().isBlank()) {
            throw new IdentityUnavailableException("the Keycloak Admin API is not configured", null);
        }
    }

    private String bearer() {
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
        return "Bearer " + token.path("access_token").asText();
    }

    /**
     * Creates the realm role {@code name} with {@code description}; a role that already exists (409)
     * counts as created, so a retried or repeated create converges (I1: a deleted KB's role stays).
     *
     * @return true when this call created it, false when it existed
     * @throws IdentityUnavailableException when Keycloak cannot be reached, refuses, or is too slow
     */
    public boolean ensureRealmRole(String name, String description) {
        requireConfigured();
        try {
            String bearer = bearer();
            Map<String, Object> role = new LinkedHashMap<>();
            role.put("name", name);
            role.put("description", description);
            try {
                client.post()
                        .uri(admin.baseUrl() + "/admin/realms/{realm}/roles", admin.realm())
                        .header("Authorization", bearer)
                        .contentType(MediaType.APPLICATION_JSON)
                        .body(role)
                        .retrieve()
                        .toBodilessEntity();
                return true;
            } catch (HttpClientErrorException.Conflict exists) {
                return false;
            }
        } catch (RestClientException | NullPointerException e) {
            throw new IdentityUnavailableException("Keycloak Admin API: " + e.getMessage(), e);
        }
    }

    /** The realm roles named {@code policy:*}, sorted. */
    public List<String> policyRoles() {
        requireConfigured();
        try {
            String bearer = bearer();
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
