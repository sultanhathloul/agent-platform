package sa.ncai.agent.gateway.attachments;

import static org.assertj.core.api.Assertions.assertThat;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import sa.ncai.agent.gateway.GatewayTestSupport;

/**
 * An upload over the limit, over a real connection (owner decision R3): the client gets the JSON
 * 413 {@code file_too_large}, never a reset connection, whether it declares its length (refused by
 * {@link AttachmentGate} before the body is read) or sends it chunked (refused by the multipart
 * caps derived from config/docs.yaml).
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class UploadOverHttpTest extends GatewayTestSupport {

    private static final int OVERSIZE = 40 * 1024 * 1024;

    @LocalServerPort
    int port;

    @Autowired
    ObjectMapper mapper;

    @Autowired
    UploadRateLimiter rateLimiter;

    private final HttpClient client =
            HttpClient.newBuilder().version(HttpClient.Version.HTTP_1_1).connectTimeout(Duration.ofSeconds(10)).build();

    @BeforeEach
    void freshAllowance() {
        rateLimiter.clear();
    }

    private static byte[] multipart(String boundary) {
        ByteArrayOutputStream body = new ByteArrayOutputStream();
        body.writeBytes(("--" + boundary + "\r\nContent-Disposition: form-data; name=\"file\"; filename=\"big.pdf\"\r\n"
                + "Content-Type: application/pdf\r\n\r\n%PDF-1.7\n").getBytes(StandardCharsets.UTF_8));
        body.writeBytes(new byte[OVERSIZE]);
        body.writeBytes(("\r\n--" + boundary + "--\r\n").getBytes(StandardCharsets.UTF_8));
        return body.toByteArray();
    }

    private HttpResponse<String> upload(HttpRequest.BodyPublisher body, String boundary) throws Exception {
        String bearer = bearer(token(claims -> claims.subject("viewer-1")
                .claim("realm_access", Map.of("roles", List.of("user", "cap:general", "cap:docs")))));
        HttpRequest request = HttpRequest.newBuilder(URI.create(
                        "http://localhost:" + port + "/v1/conversations/" + UUID.randomUUID() + "/attachments"))
                .header("Authorization", bearer)
                .header("Content-Type", "multipart/form-data; boundary=" + boundary)
                .timeout(Duration.ofSeconds(60))
                .POST(body)
                .build();
        return client.send(request, HttpResponse.BodyHandlers.ofString());
    }

    private void assertJson413(HttpResponse<String> response) throws Exception {
        assertThat(response.statusCode()).isEqualTo(413);
        JsonNode body = mapper.readTree(response.body());
        assertThat(body.path("error").path("code").asText()).isEqualTo("file_too_large");
        assertThat(body.path("error").path("message_ar").asText()).isEqualTo("حجم الملف أكبر من الحد المسموح به.");
    }

    @Test
    void aDeclaredOversizeUploadGetsTheJson413NotAReset() throws Exception {
        String boundary = "b" + UUID.randomUUID();
        assertJson413(upload(HttpRequest.BodyPublishers.ofByteArray(multipart(boundary)), boundary));
    }

    @Test
    void aChunkedOversizeUploadGetsTheJson413Too() throws Exception {
        String boundary = "b" + UUID.randomUUID();
        byte[] body = multipart(boundary);
        assertJson413(upload(HttpRequest.BodyPublishers.ofInputStream(() -> new ByteArrayInputStream(body)), boundary));
    }
}
