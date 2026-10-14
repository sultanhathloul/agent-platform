package sa.ncai.agent.gateway.attachments;

import static org.assertj.core.api.Assertions.assertThat;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.mock.web.MockHttpServletRequest;
import sa.ncai.agent.gateway.GatewayTestSupport;

/**
 * No spelling of the upload path reaches the upload handler around the gate (the verification of
 * review round 2: {@code %61ttachments} did). Each spelling is either rejected with 400 by the
 * firewall, or it goes through the whole gate: cap:docs (403), then rate, size and concurrency
 * (here 429 {@code too_many_uploads}, the caller's slots being held). Raw sockets, so the path is
 * sent exactly as written, never normalised by a client library.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class UploadPathTest extends GatewayTestSupport {

    /** Spellings of {@code /v1/conversations/{id}/attachments}; {@code {id}} is replaced. */
    private static final List<String> SPELLINGS = List.of(
            "/v1/conversations/{id}/%61ttachments",          // percent-encoded letter
            "/v1/conversations/{id}/%2561ttachments",        // double-encoded
            "/v1/conversations/{id}/attachme%6Ets",          // upper-case hex
            "/v1/conversations/{id}/attachme%6ets",          // lower-case hex
            "/v1/conversations/{id}//attachments",           // doubled slash
            "/v1/conversations/{id}/./attachments",          // dot segment
            "/v1/conversations/{id}/attachments;x=1",        // matrix parameter
            "/v1/conversations/{id}/attachments/",           // trailing slash
            "/v1/conversations/{id}/attachments");           // the plain path, for contrast

    @LocalServerPort
    int port;

    @Autowired
    UploadRateLimiter rateLimiter;

    @Autowired
    UploadConcurrency concurrency;

    @BeforeEach
    void fresh() {
        rateLimiter.clear();
        concurrency.clear();
    }

    @Test
    void withoutCapDocsEverySpellingIsRejectedByTheFirewallOrForbiddenByTheGate() throws Exception {
        for (String spelling : SPELLINGS) {
            Response response = upload(spelling, "user", "cap:general");
            assertThat(response.status).as(spelling).isIn(400, 403);
            if (response.status == 400) {
                assertThat(response.code).as(spelling).isEqualTo("invalid_request");
            } else {
                assertThat(response.code).as(spelling).isEqualTo("forbidden");
            }
            assertThat(response.connectionClose).as(spelling).isTrue();
        }
    }

    @Test
    void withCapDocsEverySpellingIsRejectedByTheFirewallOrMeetsTheConcurrencyCap() throws Exception {
        String subject = "path-" + UUID.randomUUID();
        // The caller's two slots are held, so a request the gate sees is 429 too_many_uploads,
        // and one that went around the gate would reach the handler instead.
        assertThat(concurrency.tryAcquire("tenant-a/" + subject)).isTrue();
        assertThat(concurrency.tryAcquire("tenant-a/" + subject)).isTrue();
        for (String spelling : SPELLINGS) {
            Response response = uploadAs(subject, spelling, "user", "cap:general", "cap:docs");
            assertThat(response.status).as(spelling).isIn(400, 429);
            assertThat(response.code).as(spelling).isIn("invalid_request", "too_many_uploads");
        }
        // The plain path and the trailing slash pass the firewall and meet the gate.
        assertThat(uploadAs(subject, SPELLINGS.get(8), "user", "cap:docs").code).isEqualTo("too_many_uploads");
        assertThat(uploadAs(subject, SPELLINGS.get(7), "user", "cap:docs").code).isEqualTo("too_many_uploads");
        // Every encoded, dotted, doubled or matrix spelling is the firewall's 400.
        for (String spelling : SPELLINGS.subList(0, 7)) {
            assertThat(uploadAs(subject, spelling, "user", "cap:docs").status).as(spelling).isEqualTo(400);
        }
    }

    /** The gate matches the path Spring MVC routes on, whatever the firewall lets through. */
    @Test
    void theGateMatchesTheDecodedPathWithoutMatrixParameters() {
        String id = UUID.randomUUID().toString();
        assertThat(AttachmentGate.lookupPath(new MockHttpServletRequest("POST", "/v1/conversations/" + id + "/%61ttachments")))
                .isEqualTo("/v1/conversations/" + id + "/attachments");
        assertThat(AttachmentGate.lookupPath(new MockHttpServletRequest("POST", "/v1/conversations/" + id + "/attachme%6Ets")))
                .isEqualTo("/v1/conversations/" + id + "/attachments");
        assertThat(AttachmentGate.lookupPath(new MockHttpServletRequest("POST", "/v1/conversations/" + id + "/attachments;x=1")))
                .isEqualTo("/v1/conversations/" + id + "/attachments");
    }

    private Response upload(String spelling, String... roles) throws Exception {
        return uploadAs("path-" + UUID.randomUUID(), spelling, roles);
    }

    /** A small complete multipart upload of a text file, sent on a raw socket with the path as written. */
    private Response uploadAs(String subject, String spelling, String... roles) throws Exception {
        String path = spelling.replace("{id}", UUID.randomUUID().toString());
        String boundary = "b" + UUID.randomUUID();
        String body = "--" + boundary + "\r\n"
                + "Content-Disposition: form-data; name=\"file\"; filename=\"a.txt\"\r\n"
                + "Content-Type: text/plain\r\n\r\n"
                + "hello\r\n"
                + "--" + boundary + "--\r\n";
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        String bearer = bearer(token(claims -> claims.subject(subject)
                .claim("realm_access", Map.of("roles", List.of(roles)))));
        try (Socket socket = new Socket()) {
            socket.connect(new InetSocketAddress("localhost", port), 5000);
            socket.setSoTimeout(10000);
            OutputStream out = socket.getOutputStream();
            String head = "POST " + path + " HTTP/1.1\r\n"
                    + "Host: localhost\r\n"
                    + "Authorization: " + bearer + "\r\n"
                    + "Content-Type: multipart/form-data; boundary=" + boundary + "\r\n"
                    + "Content-Length: " + bytes.length + "\r\n"
                    + "Connection: close\r\n\r\n";
            out.write(head.getBytes(StandardCharsets.UTF_8));
            out.write(bytes);
            out.flush();
            return Response.read(socket.getInputStream());
        }
    }

    private record Response(int status, String code, boolean connectionClose) {

        static Response read(InputStream in) throws Exception {
            ByteArrayOutputStream buffer = new ByteArrayOutputStream();
            try {
                in.transferTo(buffer);
            } catch (Exception reset) {
                // a reset after the response arrived: parse what came
            }
            String text = buffer.toString(StandardCharsets.UTF_8);
            int status = text.startsWith("HTTP/1.1 ") ? Integer.parseInt(text.substring(9, 12)) : -1;
            String code = null;
            int marker = text.indexOf("\"code\":\"");
            if (marker >= 0) {
                int start = marker + "\"code\":\"".length();
                code = text.substring(start, text.indexOf('"', start));
            }
            return new Response(status, code, text.toLowerCase().contains("connection: close"));
        }
    }
}
