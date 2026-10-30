package sa.ncai.agent.gateway.proxy;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.net.http.HttpTimeoutException;
import java.time.Duration;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.function.Function;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatusCode;
import org.springframework.http.client.AbstractClientHttpRequest;
import org.springframework.http.client.ClientHttpRequest;
import org.springframework.http.client.ClientHttpRequestFactory;
import org.springframework.http.client.ClientHttpResponse;

/**
 * The turn stream's requests to the agent, with two bounds (the PR #12 review, decision 5,
 * ADR-0264): the agent's response headers within {@code headers} (15 s, config/turn.yaml
 * gateway_headers_s, past the agent's own admission bound of 10 s), and the whole exchange, from
 * the request to the body's last byte, within {@code total}: deadline_s + gateway_margin_s (135 s),
 * or, when the agent's headers declare a profile's deadline ({@code X-Turn-Deadline-S}, a report turn's
 * 300 s), that deadline + the margin, never past the longest deadline in force + the margin (ADR-0379).
 * Spring's JDK factory has one read timeout covering both (ADR-0256), so this factory uses the
 * JDK client itself: the request's own timeout bounds the headers (the JDK's semantics: until the
 * response is received, which for a streamed body is its headers), and the body is closed at the
 * total bound, which closes the connection to the agent and so ends its turn there. Built from
 * the same {@link HttpClient} as every other agent call, so connection reuse and keep-alive
 * (ADR-0260) are shared; the RestClient around it still adds the trace headers.
 */
final class TurnStreamRequestFactory implements ClientHttpRequestFactory {

    /** Headers the JDK client sets itself and refuses from a caller (as Spring's JDK request skips). */
    private static final Set<String> RESTRICTED = Set.of("connection", "content-length", "expect", "host", "upgrade");

    private static final ScheduledExecutorService CLOSER = Executors.newSingleThreadScheduledExecutor(runnable -> {
        Thread thread = new Thread(runnable, "turn-stream-bound");
        thread.setDaemon(true);
        return thread;
    });

    private final HttpClient http;
    private final Duration headers;
    /**
     * The whole exchange's bound from the agent's {@code X-Turn-Deadline-S} (null when absent), read
     * when its response headers arrive: the deadlines in force then (ADR-0283, ADR-0379).
     */
    private final Function<String, Duration> total;

    TurnStreamRequestFactory(HttpClient http, Duration headers, Function<String, Duration> total) {
        this.http = http;
        this.headers = headers;
        this.total = total;
    }

    @Override
    public ClientHttpRequest createRequest(URI uri, HttpMethod method) {
        return new Request(uri, method);
    }

    private final class Request extends AbstractClientHttpRequest {

        private final URI uri;
        private final HttpMethod method;
        private final ByteArrayOutputStream body = new ByteArrayOutputStream(1024);

        Request(URI uri, HttpMethod method) {
            this.uri = uri;
            this.method = method;
        }

        @Override
        public HttpMethod getMethod() {
            return method;
        }

        @Override
        public URI getURI() {
            return uri;
        }

        @Override
        protected OutputStream getBodyInternal(HttpHeaders headers) {
            return body;
        }

        @Override
        protected ClientHttpResponse executeInternal(HttpHeaders requestHeaders) throws IOException {
            long started = System.nanoTime();
            HttpRequest.Builder builder = HttpRequest.newBuilder(uri)
                    .timeout(headers)
                    .method(method.name(), HttpRequest.BodyPublishers.ofByteArray(body.toByteArray()));
            requestHeaders.forEach((name, values) -> {
                if (!RESTRICTED.contains(name.toLowerCase(Locale.ROOT))) {
                    values.forEach(value -> builder.header(name, value));
                }
            });
            CompletableFuture<HttpResponse<InputStream>> sent =
                    http.sendAsync(builder.build(), HttpResponse.BodyHandlers.ofInputStream());
            HttpResponse<InputStream> response;
            try {
                response = sent.get();
            } catch (InterruptedException e) {
                sent.cancel(true);
                Thread.currentThread().interrupt();
                throw new IOException("interrupted waiting for the agent's response headers", e);
            } catch (ExecutionException e) {
                if (e.getCause() instanceof HttpTimeoutException) {
                    throw new IOException("no response headers from the agent within " + headers.toMillis() + " ms",
                            e.getCause());
                }
                throw e.getCause() instanceof IOException io ? io : new IOException(e.getCause());
            }
            Duration totalBound = total.apply(response.headers().firstValue(AgentClient.TURN_DEADLINE_HEADER).orElse(null));
            long left = totalBound.toNanos() - (System.nanoTime() - started);
            InputStream stream = response.body();
            ScheduledFuture<?> bound = CLOSER.schedule(() -> closeQuietly(stream), Math.max(left, 0),
                    TimeUnit.NANOSECONDS);
            return new Response(response, stream, bound);
        }
    }

    private static final class Response implements ClientHttpResponse {

        private final HttpResponse<InputStream> response;
        private final InputStream body;
        private final ScheduledFuture<?> bound;
        private final HttpHeaders headers = new HttpHeaders();

        Response(HttpResponse<InputStream> response, InputStream body, ScheduledFuture<?> bound) {
            this.response = response;
            this.body = body;
            this.bound = bound;
            response.headers().map().forEach((name, values) -> headers.addAll(name, List.copyOf(values)));
        }

        @Override
        public HttpStatusCode getStatusCode() {
            return HttpStatusCode.valueOf(response.statusCode());
        }

        @Override
        public String getStatusText() {
            return "";
        }

        @Override
        public HttpHeaders getHeaders() {
            return headers;
        }

        @Override
        public InputStream getBody() {
            return body;
        }

        @Override
        public void close() {
            bound.cancel(false);
            closeQuietly(body);
        }
    }

    private static void closeQuietly(InputStream stream) {
        try {
            stream.close();
        } catch (IOException ignored) {
            // the connection is gone either way
        }
    }
}
