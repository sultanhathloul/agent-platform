package sa.ncai.agent.gateway.attachments;

import static org.assertj.core.api.Assertions.assertThat;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import okhttp3.mockwebserver.MockResponse;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import sa.ncai.agent.gateway.GatewayTestSupport;

/**
 * Uploads over a real connection (owner decisions R3 and review round 2, finding 2). An upload over
 * the limit is refused with the JSON 413 {@code file_too_large} before its body is read; a refused
 * upload's body is never drained — the gateway swallows none of it and closes the socket — so a
 * client that declares a huge body and trickles it cannot hold a connection open; and many such
 * refused connections do not starve other traffic (a chat turn still completes).
 *
 * The refusal cases use raw sockets, not {@link HttpClient}: because the gateway closes the socket
 * as soon as it writes the refusal, a client that is still streaming a large request body sees the
 * close mid-send (which is the point), and a raw socket reads the refusal response deterministically.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class UploadOverHttpTest extends GatewayTestSupport {

    private static final String DONE_STREAM =
            """
            event: done
            id: 1
            data: {"type":"done","request_id":"r","turn_id":"t","seq":1,"data":{"answer":"مرحباً","citations":[],"usage":{"tokens_in":1,"tokens_out":1,"cost_usd":0.0},"guard_events":[],"state_digest":{},"trace_id":"4bf92f3577b34da6a3ce929d0e0e4736"}}

            """;

    @LocalServerPort
    int port;

    @Autowired
    UploadRateLimiter rateLimiter;

    @Autowired
    UploadConcurrency concurrency;

    private final HttpClient client =
            HttpClient.newBuilder().version(HttpClient.Version.HTTP_1_1).connectTimeout(Duration.ofSeconds(10)).build();

    @BeforeEach
    void freshAllowance() {
        rateLimiter.clear();
        concurrency.clear();
    }

    private String bearerFor(String subject) {
        return bearer(token(claims -> claims.subject(subject)
                .claim("realm_access", Map.of("roles", List.of("user", "cap:general", "cap:docs")))));
    }

    @Test
    void anUploadDeclaringAnOversizeBodyGets413BeforeTheBodyIsRead() throws Exception {
        // 30 MB declared, over the 20 MB + envelope limit. Only the headers are sent; the gateway
        // refuses on the declared length alone, before any body.
        try (RawRefusal refusal = sendUpload("viewer-1", 30L * 1024 * 1024, false)) {
            assertThat(refusal.status).isEqualTo(413);
            assertThat(refusal.code).isEqualTo("file_too_large");
            assertThat(refusal.connectionClose).isTrue();
        }
    }

    /**
     * A client that declares a 10^12-byte body and only trickles bytes is refused with the JSON 413
     * and its connection is closed within 5 s, so it cannot pin the connection open (finding 2).
     */
    @Test
    void aTrickledHugeUploadIs413AndTheConnectionClosesWithinFiveSeconds() throws Exception {
        try (RawRefusal refusal = sendUpload("viewer-1", 1_000_000_000_000L, true)) {
            assertThat(refusal.status).isEqualTo(413);
            assertThat(refusal.code).isEqualTo("file_too_large");
            assertThat(refusal.connectionClose).isTrue();
            assertThat(refusal.closedWithin(Duration.ofSeconds(5)))
                    .as("the server closed the socket within 5 s")
                    .isTrue();
        }
    }

    /**
     * 200 connections each declaring a huge upload body are all refused without draining, so Tomcat
     * threads are freed at once and an unrelated chat turn completes while they are in flight
     * (finding 2). Distinct subjects keep the per-user rate limit from turning some 413s into 429s.
     */
    @Test
    void otherTrafficCompletesWhileManyRefusedUploadsAreInFlight() throws Exception {
        List<RawRefusal> attempts = new ArrayList<>();
        try {
            for (int i = 0; i < 200; i++) {
                attempts.add(sendUpload("flooder-" + i, 1_000_000_000_000L, true));
            }
            // An unrelated user's chat turn, over the same server, completes promptly.
            AGENT.enqueue(new MockResponse()
                    .setHeader("Content-Type", "text/event-stream; charset=utf-8")
                    .setBody(DONE_STREAM));
            String conversation = UUID.randomUUID().toString();
            HttpRequest chat = HttpRequest.newBuilder(URI.create("http://localhost:" + port + "/v1/chat/turns"))
                    .header("Authorization", bearerFor("chatter-1"))
                    .header("Content-Type", "application/json")
                    .header("Accept", "text/event-stream")
                    .timeout(Duration.ofSeconds(20))
                    .POST(HttpRequest.BodyPublishers.ofString(
                            "{\"conversation_id\":\"" + conversation + "\",\"message\":\"مرحبا\"}"))
                    .build();
            CompletableFuture<HttpResponse<String>> turn = client.sendAsync(chat, HttpResponse.BodyHandlers.ofString());
            HttpResponse<String> response = turn.get(15, TimeUnit.SECONDS);
            assertThat(response.statusCode()).isEqualTo(200);
            assertThat(response.body()).contains("\"type\":\"done\"");

            // Every refused upload got a 4xx and a close; none was left draining.
            for (RawRefusal attempt : attempts) {
                assertThat(attempt.status).isBetween(400, 499);
                assertThat(attempt.connectionClose).isTrue();
            }
        } finally {
            for (RawRefusal attempt : attempts) {
                attempt.close();
            }
        }
    }

    /**
     * Opens a raw socket and sends an upload request declaring {@code contentLength}; with
     * {@code withFragment} a few body bytes follow the headers (a trickle). The gateway refuses on
     * the declared length before the body, so the response is read right away.
     */
    private RawRefusal sendUpload(String subject, long contentLength, boolean withFragment) throws Exception {
        String boundary = "b" + UUID.randomUUID();
        Socket socket = new Socket();
        socket.connect(new InetSocketAddress("localhost", port), 5000);
        socket.setSoTimeout(6000);
        OutputStream out = socket.getOutputStream();
        String head = "POST /v1/conversations/" + UUID.randomUUID() + "/attachments HTTP/1.1\r\n"
                + "Host: localhost\r\n"
                + "Authorization: " + bearerFor(subject) + "\r\n"
                + "Content-Type: multipart/form-data; boundary=" + boundary + "\r\n"
                + "Content-Length: " + contentLength + "\r\n\r\n"
                + "--" + boundary + "\r\n";
        out.write(head.getBytes(StandardCharsets.UTF_8));
        out.flush();
        if (withFragment) {
            out.write("Content-Disposition: form-data; name=\"file\"".getBytes(StandardCharsets.UTF_8));
            out.flush();
        }
        return RawRefusal.read(socket);
    }

    /** One raw refusal response and the socket it came on, so a test can see it close. */
    private static final class RawRefusal implements AutoCloseable {
        final Socket socket;
        final int status;
        final String code;
        final boolean connectionClose;
        private final InputStream in;

        private RawRefusal(Socket socket, InputStream in, int status, String code, boolean connectionClose) {
            this.socket = socket;
            this.in = in;
            this.status = status;
            this.code = code;
            this.connectionClose = connectionClose;
        }

        static RawRefusal read(Socket socket) throws Exception {
            InputStream in = socket.getInputStream();
            ByteArrayOutputStream buffer = new ByteArrayOutputStream();
            int status = -1;
            boolean close = false;
            String code = null;
            try {
                int b;
                while ((b = in.read()) != -1 && buffer.size() < 65536) {
                    buffer.write(b);
                    byte[] seen = buffer.toByteArray();
                    // Once the headers are complete, parse the status line and the Connection header.
                    if (status < 0 && endOfHeaders(seen)) {
                        String headers = new String(seen, StandardCharsets.ISO_8859_1);
                        status = headers.startsWith("HTTP/1.1 ") ? Integer.parseInt(headers.substring(9, 12)) : -1;
                        close = headers.toLowerCase().contains("connection: close");
                    }
                }
            } catch (Exception reset) {
                // A reset after the response was buffered is fine; parse whatever arrived.
            }
            String text = buffer.toString(StandardCharsets.UTF_8);
            if (status < 0 && text.startsWith("HTTP/1.1 ")) {
                status = Integer.parseInt(text.substring(9, 12));
                close = text.toLowerCase().contains("connection: close");
            }
            int marker = text.indexOf("\"code\":\"");
            if (marker >= 0) {
                int start = marker + "\"code\":\"".length();
                code = text.substring(start, text.indexOf('"', start));
            }
            return new RawRefusal(socket, in, status, code, close);
        }

        private static boolean endOfHeaders(byte[] b) {
            return b.length >= 4 && b[b.length - 4] == '\r' && b[b.length - 3] == '\n'
                    && b[b.length - 2] == '\r' && b[b.length - 1] == '\n';
        }

        /** True when the server has closed the socket (read returns -1 or throws) within the timeout. */
        boolean closedWithin(Duration timeout) throws Exception {
            socket.setSoTimeout((int) timeout.toMillis());
            try {
                return in.read() == -1;
            } catch (java.net.SocketTimeoutException stillOpen) {
                return false;
            } catch (Exception resetIsClosed) {
                return true;
            }
        }

        @Override
        public void close() {
            try {
                socket.close();
            } catch (Exception ignored) {
                // nothing to do
            }
        }
    }
}
