package sa.ncai.agent.gateway.attachments;

import static org.assertj.core.api.Assertions.assertThat;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicBoolean;
import okhttp3.mockwebserver.MockResponse;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import sa.ncai.agent.gateway.GatewayTestSupport;

/**
 * The gateway-wide ceiling behind the per-user upload limits (the verification of review round 2).
 * Here config/docs.yaml is copied with a gateway-wide cap of 2 (so one user can hold all of it) and
 * a 4-second deadline, so the test runs in seconds; the shipped values are 50 and 120 s.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class UploadCeilingTest extends GatewayTestSupport {

    private static final Duration DEADLINE = Duration.ofSeconds(4);

    private static final String DONE_STREAM =
            """
            event: done
            id: 1
            data: {"type":"done","request_id":"r","turn_id":"t","seq":1,"data":{"answer":"مرحباً","citations":[],"usage":{"tokens_in":1,"tokens_out":1,"cost_usd":0.0},"guard_events":[],"state_digest":{},"trace_id":"4bf92f3577b34da6a3ce929d0e0e4736"}}

            """;

    @DynamicPropertySource
    static void smallCeiling(DynamicPropertyRegistry registry) throws IOException {
        String shipped = System.getenv().getOrDefault("GATEWAY_DOCS_CONFIG", "../../config/docs.yaml");
        String yaml = Files.readString(Path.of(shipped), StandardCharsets.UTF_8)
                .replaceFirst("max_concurrent_uploads_total: \\d+", "max_concurrent_uploads_total: 2")
                .replaceFirst("upload_deadline_seconds: \\d+", "upload_deadline_seconds: " + DEADLINE.toSeconds());
        Path copy = Files.createTempFile("docs-ceiling-", ".yaml");
        Files.writeString(copy, yaml, StandardCharsets.UTF_8);
        registry.add("gateway.docs.config", copy::toString);
    }

    @LocalServerPort
    int port;

    @Autowired
    UploadRateLimiter rateLimiter;

    @Autowired
    UploadConcurrency concurrency;

    @Autowired
    DocsLimits limits;

    private final HttpClient client =
            HttpClient.newBuilder().version(HttpClient.Version.HTTP_1_1).connectTimeout(Duration.ofSeconds(10)).build();

    @BeforeEach
    void fresh() {
        rateLimiter.clear();
        concurrency.clear();
    }

    @Test
    void theShippedConfigCarriesTheCeilingAndThisTestShrinksIt() throws Exception {
        String shipped = System.getenv().getOrDefault("GATEWAY_DOCS_CONFIG", "../../config/docs.yaml");
        DocsLimits real = DocsLimits.load(Path.of(shipped));
        assertThat(real.maxConcurrentUploadsTotal()).isEqualTo(50);
        assertThat(real.uploadDeadline()).isEqualTo(Duration.ofSeconds(120));
        assertThat(limits.maxConcurrentUploadsTotal()).isEqualTo(2);
        assertThat(limits.uploadDeadline()).isEqualTo(DEADLINE);
    }

    /**
     * One user holds the whole gateway-wide cap with two trickling uploads. Another user's upload is
     * refused 429 {@code too_many_uploads} with a close, and a third user's chat turn completes.
     */
    @Test
    void whileOneUserHoldsTheGlobalCapAnotherUsersChatTurnCompletes() throws Exception {
        List<Trickle> held = new ArrayList<>();
        try {
            held.add(Trickle.start(port, bearerFor("holder")));
            held.add(Trickle.start(port, bearerFor("holder")));
            Thread.sleep(500); // both admitted and reading their bodies

            Trickle refused = Trickle.start(port, bearerFor("other"));
            String answer = refused.response(Duration.ofSeconds(5));
            assertThat(answer).startsWith("HTTP/1.1 429");
            assertThat(answer).contains("\"code\":\"too_many_uploads\"");
            assertThat(answer.toLowerCase()).contains("connection: close");
            refused.close();

            AGENT.enqueue(new MockResponse()
                    .setHeader("Content-Type", "text/event-stream; charset=utf-8")
                    .setBody(DONE_STREAM));
            HttpRequest chat = HttpRequest.newBuilder(URI.create("http://localhost:" + port + "/v1/chat/turns"))
                    .header("Authorization", bearerFor("chatter"))
                    .header("Content-Type", "application/json")
                    .header("Accept", "text/event-stream")
                    .timeout(Duration.ofSeconds(3))
                    .POST(HttpRequest.BodyPublishers.ofString(
                            "{\"conversation_id\":\"" + UUID.randomUUID() + "\",\"message\":\"مرحبا\"}"))
                    .build();
            HttpResponse<String> turn = client.send(chat, HttpResponse.BodyHandlers.ofString());
            assertThat(turn.statusCode()).isEqualTo(200);
            assertThat(turn.body()).contains("\"type\":\"done\"");
            // The holder's uploads were still in flight while the turn ran.
            for (Trickle trickle : held) {
                assertThat(trickle.isOpen()).isTrue();
            }
        } finally {
            held.forEach(Trickle::close);
        }
    }

    /**
     * An admitted upload that keeps trickling — never stalling long enough for the per-read timeout —
     * is cut at the deadline with 408 {@code upload_timeout} and a close, and its slots are freed.
     */
    @Test
    void aTrickledUploadPastItsDeadlineIs408AndFreesItsSlot() throws Exception {
        long start = System.nanoTime();
        try (Trickle trickle = Trickle.start(port, bearerFor("slow"))) {
            String answer = trickle.response(DEADLINE.plusSeconds(6));
            Duration took = Duration.ofNanos(System.nanoTime() - start);
            assertThat(answer).startsWith("HTTP/1.1 408");
            assertThat(answer).contains("\"code\":\"upload_timeout\"");
            assertThat(answer.toLowerCase()).contains("connection: close");
            assertThat(took).isGreaterThanOrEqualTo(DEADLINE).isLessThan(DEADLINE.plusSeconds(4));
        }
        Thread.sleep(300);
        // Both slots are free again: the same user is admitted twice, and only twice.
        assertThat(concurrency.tryAcquire("tenant-a/slow")).isTrue();
        assertThat(concurrency.tryAcquire("tenant-a/slow")).isTrue();
        assertThat(concurrency.tryAcquire("tenant-a/slow")).isFalse();
    }

    private String bearerFor(String subject) {
        return bearer(token(claims -> claims.subject(subject)
                .claim("realm_access", Map.of("roles", List.of("user", "cap:general", "cap:docs")))));
    }

    /** An upload on a raw socket whose body is written one byte every 200 ms, never finishing. */
    private static final class Trickle implements AutoCloseable {

        private final Socket socket;
        private final AtomicBoolean running = new AtomicBoolean(true);

        private Trickle(Socket socket) {
            this.socket = socket;
        }

        static Trickle start(int port, String bearer) throws IOException {
            Socket socket = new Socket();
            socket.connect(new InetSocketAddress("localhost", port), 5000);
            String boundary = "b" + UUID.randomUUID();
            String head = "POST /v1/conversations/" + UUID.randomUUID() + "/attachments HTTP/1.1\r\n"
                    + "Host: localhost\r\n"
                    + "Authorization: " + bearer + "\r\n"
                    + "Content-Type: multipart/form-data; boundary=" + boundary + "\r\n"
                    + "Content-Length: 1000000\r\n\r\n"
                    + "--" + boundary + "\r\n"
                    + "Content-Disposition: form-data; name=\"file\"; filename=\"a.txt\"\r\n"
                    + "Content-Type: text/plain\r\n\r\n";
            OutputStream out = socket.getOutputStream();
            out.write(head.getBytes(StandardCharsets.UTF_8));
            out.flush();
            Trickle trickle = new Trickle(socket);
            Thread.ofVirtual().start(() -> {
                try {
                    while (trickle.running.get()) {
                        out.write('x');
                        out.flush();
                        Thread.sleep(200);
                    }
                } catch (Exception closed) {
                    // the server closed the connection, or the test did
                }
            });
            return trickle;
        }

        /** Everything the server sends until it closes the connection, or until the timeout. */
        String response(Duration timeout) throws IOException {
            socket.setSoTimeout((int) timeout.toMillis());
            InputStream in = socket.getInputStream();
            ByteArrayOutputStream buffer = new ByteArrayOutputStream();
            try {
                in.transferTo(buffer);
            } catch (IOException resetOrTimeout) {
                // parse what arrived
            }
            running.set(false);
            return buffer.toString(StandardCharsets.UTF_8);
        }

        /** True while the server has neither answered nor closed: the upload is still being read. */
        boolean isOpen() throws IOException {
            socket.setSoTimeout(50);
            try {
                socket.getInputStream().read();
                return false;
            } catch (java.net.SocketTimeoutException nothingYet) {
                return true;
            } catch (IOException closed) {
                return false;
            }
        }

        @Override
        public void close() {
            running.set(false);
            try {
                socket.close();
            } catch (IOException ignored) {
                // nothing to do
            }
        }
    }
}
