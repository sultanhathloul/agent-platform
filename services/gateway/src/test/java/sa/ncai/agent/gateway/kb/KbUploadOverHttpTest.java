package sa.ncai.agent.gateway.kb;

import static org.assertj.core.api.Assertions.assertThat;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.ByteArrayOutputStream;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.TimeUnit;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import sa.ncai.agent.gateway.GatewayTestSupport;
import sa.ncai.agent.gateway.attachments.UploadConcurrency;
import sa.ncai.agent.gateway.attachments.UploadRateLimiter;

/**
 * A knowledge base document's upload over a real connection (phase 08, I2): the request passes the
 * upload gate, whose deadline wrapper parses the multipart body itself, and the form fields beside the
 * file (its D3 metadata, Arabic text) still reach the controller and the agent's registration intact.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class KbUploadOverHttpTest extends GatewayTestSupport {

    @LocalServerPort
    int port;

    @Autowired
    UploadRateLimiter rateLimiter;

    @Autowired
    UploadConcurrency concurrency;

    @Autowired
    ObjectMapper mapper;

    private final HttpClient client =
            HttpClient.newBuilder().version(HttpClient.Version.HTTP_1_1).connectTimeout(Duration.ofSeconds(10)).build();

    @BeforeEach
    void freshAllowance() {
        rateLimiter.clear();
        concurrency.clear();
    }

    private static byte[] multipart(String boundary, Map<String, String> fields, String fileName, byte[] file) {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        fields.forEach((name, value) -> {
            out.writeBytes(("--" + boundary + "\r\nContent-Disposition: form-data; name=\"" + name + "\"\r\n\r\n")
                    .getBytes(StandardCharsets.UTF_8));
            out.writeBytes(value.getBytes(StandardCharsets.UTF_8));
            out.writeBytes("\r\n".getBytes(StandardCharsets.UTF_8));
        });
        out.writeBytes(("--" + boundary + "\r\nContent-Disposition: form-data; name=\"file\"; filename=\"" + fileName
                + "\"\r\nContent-Type: application/pdf\r\n\r\n").getBytes(StandardCharsets.UTF_8));
        out.writeBytes(file);
        out.writeBytes(("\r\n--" + boundary + "--\r\n").getBytes(StandardCharsets.UTF_8));
        return out.toByteArray();
    }

    @Test
    void theFormFieldsBesideTheFileReachTheAgentIntact() throws Exception {
        AGENT.enqueue(new MockResponse().setResponseCode(201).setHeader("Content-Type", "application/json").setBody("""
                {"id":"6f1c0a2e-3b4d-4e5f-8a9b-0c1d2e3f4a5b","kb":"intl-economy","name":"weo.pdf","kind":"pdf","size":42,\
                "status":"pending","metadata":{"title":"آفاق","publisher":"صندوق","published_on":"2026-07-15"},\
                "created_at":"2026-10-26T12:00:00Z"}"""));
        String admin = bearer(token(c -> c.claim("realm_access", Map.of("roles", List.of("user", "admin")))));
        Map<String, String> fields = new LinkedHashMap<>();
        fields.put("title", "آفاق الاقتصاد العالمي - يوليو 2026");
        fields.put("publisher", "صندوق النقد الدولي");
        fields.put("published_on", "2026-07-15");
        fields.put("source_url", "https://example.org/weo");
        String boundary = "b" + UUID.randomUUID();
        byte[] pdf = "%PDF-1.7\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF".getBytes(StandardCharsets.ISO_8859_1);
        HttpRequest request = HttpRequest.newBuilder(URI.create("http://localhost:" + port + "/v1/admin/kbs/intl-economy/documents"))
                .header("Authorization", admin)
                .header("Content-Type", "multipart/form-data; boundary=" + boundary)
                .POST(HttpRequest.BodyPublishers.ofByteArray(multipart(boundary, fields, "weo.pdf", pdf)))
                .timeout(Duration.ofSeconds(20))
                .build();
        HttpResponse<String> response = client.send(request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
        assertThat(response.statusCode()).as(response.body()).isEqualTo(201);
        RecordedRequest registered = AGENT.takeRequest(5, TimeUnit.SECONDS);
        JsonNode metadata = mapper.readTree(registered.getBody().readUtf8()).path("metadata");
        assertThat(metadata.path("title").asText()).isEqualTo("آفاق الاقتصاد العالمي - يوليو 2026");
        assertThat(metadata.path("publisher").asText()).isEqualTo("صندوق النقد الدولي");
        assertThat(metadata.path("published_on").asText()).isEqualTo("2026-07-15");
        assertThat(metadata.path("source_url").asText()).isEqualTo("https://example.org/weo");
    }
}
