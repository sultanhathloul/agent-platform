package sa.ncai.agent.gateway.proxy;

import java.io.IOException;
import java.io.InputStream;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.nio.charset.StandardCharsets;
import java.util.function.Supplier;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClient;
import sa.ncai.agent.gateway.GatewayProperties;
import sa.ncai.agent.gateway.api.RequestIdFilter;
import sa.ncai.agent.gateway.auth.GatewayPrincipal;
import sa.ncai.agent.gateway.memory.MemoryList;
import sa.ncai.agent.gateway.memory.MemorySettings;

/**
 * Internal client for the agent service (README §6.2). The client is built from Boot's
 * observed {@link RestClient.Builder}, so every call carries W3C {@code traceparent}.
 */
@Component
public class AgentClient {

    public static final String INTERNAL_TOKEN_HEADER = "X-Internal-Token";
    /** Name the caller on agent endpoints without a request body; always from the verified token (ADR-0059). */
    public static final String USER_HEADER = "X-User-Id";
    public static final String TENANT_HEADER = "X-Tenant-Id";
    /** The admin's username and display name on admin calls, percent-encoded UTF-8 (ADR-0123). */
    public static final String USER_NAME_HEADER = "X-User-Name";
    public static final String USER_DISPLAY_NAME_HEADER = "X-User-Display-Name";

    /** Consumes the agent's SSE body; runs only after a 2xx response. */
    @FunctionalInterface
    public interface StreamHandler<T> {
        T handle(InputStream body) throws IOException;
    }

    private final RestClient client;
    private final String internalToken;

    public AgentClient(RestClient.Builder builder, GatewayProperties properties) {
        GatewayProperties.Agent agent = properties.agent();
        HttpClient http = HttpClient.newBuilder()
                .version(HttpClient.Version.HTTP_1_1)
                .connectTimeout(agent.connectTimeout())
                .build();
        JdkClientHttpRequestFactory requestFactory = new JdkClientHttpRequestFactory(http);
        // Bounds the wait for response headers only; the body stream itself is unbounded.
        requestFactory.setReadTimeout(agent.responseTimeout());
        this.client = builder.baseUrl(agent.baseUrl()).requestFactory(requestFactory).build();
        this.internalToken = agent.internalToken();
    }

    /**
     * Posts a turn and hands the SSE body to {@code handler}.
     *
     * @throws AgentUnavailableException if the agent is unreachable or answers non-2xx
     * @throws AgentRefusedException if the agent refused the turn before streaming (403, 409, 503)
     */
    public <T> T streamTurn(AgentTurnRequest request, String requestId, StreamHandler<T> handler) {
        try {
            return client.post()
                    .uri("/v1/turns")
                    .contentType(MediaType.APPLICATION_JSON)
                    .accept(MediaType.TEXT_EVENT_STREAM)
                    .header(INTERNAL_TOKEN_HEADER, internalToken)
                    .header(RequestIdFilter.HEADER, requestId)
                    .body(request)
                    .exchange((clientRequest, response) -> {
                        AgentRefusedException refused =
                                AgentRefusedException.forStatus(response.getStatusCode().value());
                        if (refused != null) {
                            throw refused;
                        }
                        if (!response.getStatusCode().is2xxSuccessful()) {
                            throw new AgentUnavailableException(
                                    "agent answered " + response.getStatusCode().value());
                        }
                        return handler.handle(response.getBody());
                    });
        } catch (ResourceAccessException e) {
            throw new AgentUnavailableException("agent unreachable: " + e.getMessage(), e);
        }
    }

    /**
     * Marks the reply a finished turn persisted as retracted, so the agent's Context Assembly
     * skips it (ADR-0048).
     *
     * @throws AgentUnavailableException if the agent is unreachable or answers non-2xx
     */
    public void retractTurn(String conversationId, String turnId, AgentRetractRequest request, String requestId) {
        try {
            client.post()
                    .uri("/v1/conversations/{conversationId}/turns/{turnId}/retract", conversationId, turnId)
                    .contentType(MediaType.APPLICATION_JSON)
                    .header(INTERNAL_TOKEN_HEADER, internalToken)
                    .header(RequestIdFilter.HEADER, requestId)
                    .body(request)
                    .exchange((clientRequest, response) -> {
                        if (!response.getStatusCode().is2xxSuccessful()) {
                            throw new AgentUnavailableException(
                                    "agent answered " + response.getStatusCode().value() + " to retract");
                        }
                        return null;
                    });
        } catch (ResourceAccessException e) {
            throw new AgentUnavailableException("agent unreachable: " + e.getMessage(), e);
        }
    }

    /**
     * The caller's own memory items and memory switch (README §8).
     *
     * @throws AgentUnavailableException if the agent is unreachable or answers non-2xx
     */
    public MemoryList listMemory(GatewayPrincipal caller, String requestId) {
        return reach(() -> identified(client.get().uri("/v1/memory"), caller, requestId)
                .exchange((clientRequest, response) -> {
                    requireSuccess(response.getStatusCode().value(), "memory list");
                    return response.bodyTo(MemoryList.class);
                }));
    }

    /**
     * Deletes one of the caller's memory items.
     *
     * @return false when the caller has no item with this id, another user's item included
     * @throws AgentUnavailableException if the agent is unreachable or answers another non-2xx
     */
    public boolean deleteMemory(GatewayPrincipal caller, String itemId, String requestId) {
        return reach(() -> identified(client.delete().uri("/v1/memory/{itemId}", itemId), caller, requestId)
                .exchange((clientRequest, response) -> {
                    int status = response.getStatusCode().value();
                    if (status == 404) {
                        return false;
                    }
                    requireSuccess(status, "memory delete");
                    return true;
                }));
    }

    /**
     * Turns the caller's memory on or off.
     *
     * @throws AgentUnavailableException if the agent is unreachable or answers non-2xx
     */
    public MemorySettings setMemoryEnabled(GatewayPrincipal caller, boolean enabled, String requestId) {
        RestClient.RequestBodySpec put = client.put()
                .uri("/v1/memory/settings")
                .contentType(MediaType.APPLICATION_JSON)
                .body(new MemorySettings(enabled));
        return reach(() -> identified(put, caller, requestId)
                .exchange((clientRequest, response) -> {
                    requireSuccess(response.getStatusCode().value(), "memory settings");
                    return response.bodyTo(MemorySettings.class);
                }));
    }

    /**
     * Calls an admin endpoint (guard policies, phase 03) with the caller from the verified token,
     * and returns the status and body whatever they are; the caller maps them.
     *
     * @throws AgentUnavailableException if the agent is unreachable
     */
    public AgentReply exchangeAdmin(
            HttpMethod method, String uri, Object body, GatewayPrincipal caller, String requestId) {
        RestClient.RequestBodySpec spec = client.method(method).uri(uri);
        if (body != null) {
            spec = spec.contentType(MediaType.APPLICATION_JSON).body(body);
        }
        RestClient.RequestBodySpec request = spec;
        return reach(() -> identified(request, caller, requestId)
                .header(USER_NAME_HEADER, encoded(caller.username()))
                .header(USER_DISPLAY_NAME_HEADER, encoded(caller.displayName()))
                .exchange((clientRequest, response) ->
                        new AgentReply(response.getStatusCode().value(), response.getBody().readAllBytes())));
    }

    private RestClient.RequestHeadersSpec<?> identified(
            RestClient.RequestHeadersSpec<?> spec, GatewayPrincipal caller, String requestId) {
        return spec.header(INTERNAL_TOKEN_HEADER, internalToken)
                .header(RequestIdFilter.HEADER, requestId)
                .header(USER_HEADER, caller.userId())
                .header(TENANT_HEADER, caller.tenantId());
    }

    /** A name as an HTTP header value: UTF-8, percent-encoded (a display name may be Arabic). */
    static String encoded(String value) {
        return value == null ? "" : URLEncoder.encode(value, StandardCharsets.UTF_8).replace("+", "%20");
    }

    private static void requireSuccess(int status, String call) {
        if (status < 200 || status >= 300) {
            throw new AgentUnavailableException("agent answered " + status + " to " + call);
        }
    }

    private static <T> T reach(Supplier<T> call) {
        try {
            return call.get();
        } catch (ResourceAccessException e) {
            throw new AgentUnavailableException("agent unreachable: " + e.getMessage(), e);
        }
    }
}
