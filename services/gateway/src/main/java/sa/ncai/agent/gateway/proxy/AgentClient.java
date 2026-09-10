package sa.ncai.agent.gateway.proxy;

import java.io.IOException;
import java.io.InputStream;
import java.net.http.HttpClient;
import org.springframework.http.MediaType;
import org.springframework.http.client.JdkClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClient;
import sa.ncai.agent.gateway.GatewayProperties;
import sa.ncai.agent.gateway.api.RequestIdFilter;

/**
 * Internal client for the agent service (README §6.2). The client is built from Boot's
 * observed {@link RestClient.Builder}, so every call carries W3C {@code traceparent}.
 */
@Component
public class AgentClient {

    public static final String INTERNAL_TOKEN_HEADER = "X-Internal-Token";

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
}
