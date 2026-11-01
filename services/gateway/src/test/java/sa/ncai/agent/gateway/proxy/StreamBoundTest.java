package sa.ncai.agent.gateway.proxy;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MvcResult;
import sa.ncai.agent.gateway.GatewayTestSupport;
import sa.ncai.agent.gateway.audit.AuditEvent;

/**
 * The relay's bound on a turn stream (timeout budget PR, I3, ADR-0256), with a 1 s response timeout
 * for every other agent call and a 2 s relay bound (src/test/resources/turn-test.yaml: 1 + 1).
 *
 * <p>The finding first: Spring's JDK request factory applies its read timeout to the whole exchange,
 * body included; at the timeout it closes the body, and a relayed stream ended there as
 * {@code agent_stream_interrupted}. That, not a header timeout, is what ended phase 05's turns at
 * the 60 s gateway timeout. A turn stream now has the relay's bound instead, and it ends with
 * {@code turn_timeout}. Its response headers have a bound of their own (1 s here, 15 s shipped,
 * past the agent's admission bound; the PR #12 review, decision 5, ADR-0264): an agent that never
 * answers them is agent_unavailable at that bound. Each bound holds while another user's turn runs
 * on the same gateway at its normal time (decision 10).
 */
@TestPropertySource(properties = {
    "gateway.agent.response-timeout=1s",
    "gateway.turn.config=src/test/resources/turn-test.yaml"
})
class StreamBoundTest extends GatewayTestSupport {

    private static final String CONVERSATION_ID = "0b4c9a52-4a7e-4a8e-9f55-1d6d0c7b2e11";
    private static final long BOUND_MS = 2000;

    @Autowired
    ObjectMapper mapper;

    @Autowired
    AgentClient agentClient;

    private static String tokens(int count) {
        StringBuilder body = new StringBuilder();
        for (int i = 0; i < count; i++) {
            body.append("event: token\nid: ").append(i)
                    .append("\ndata: {\"type\":\"token\",\"request_id\":\"r\",\"turn_id\":\"t\",\"seq\":")
                    .append(i).append(",\"data\":{\"text\":\"ك\"}}\n\n");
        }
        return body.toString();
    }

    private record Relayed(List<JsonNode> events, long millis) {}

    private Relayed chatTurn() throws Exception {
        return chatTurn(token(), CONVERSATION_ID);
    }

    private Relayed chatTurn(String bearer, String conversationId) throws Exception {
        long started = System.nanoTime();
        MvcResult result = mvc.perform(post("/v1/chat/turns")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + bearer)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"conversation_id\":\"" + conversationId + "\",\"message\":\"سؤال\",\"locale\":\"ar\"}"))
                .andReturn();
        long millis = (System.nanoTime() - started) / 1_000_000;
        List<JsonNode> events = new ArrayList<>();
        for (String line : result.getResponse().getContentAsString(StandardCharsets.UTF_8).split("\n")) {
            if (line.startsWith("data: ")) {
                events.add(mapper.readTree(line.substring("data: ".length())));
            }
        }
        return new Relayed(events, millis);
    }

    @Test
    void theRelayBoundIsTheTurnFilesDeadlinePlusItsMargin() {
        assertThat(agentClient.relayBound().toMillis()).isEqualTo(BOUND_MS);
    }

    @Test
    void aStreamStillSendingIsEndedAtTheRelayBoundNotTheResponseTimeout() throws Exception {
        // Ten events over about 4 s: past the 1 s response timeout and the 2 s relay bound.
        String body = tokens(10);
        AGENT.enqueue(new MockResponse().setHeader("Content-Type", "text/event-stream")
                .setBody(body).throttleBody(body.length() / 10, 400, TimeUnit.MILLISECONDS));

        Relayed relayed = chatTurn();

        assertThat(relayed.millis()).isBetween(BOUND_MS, BOUND_MS + 1000);
        List<JsonNode> tokenEvents = relayed.events().stream().filter(e -> "token".equals(e.path("type").asText())).toList();
        assertThat(tokenEvents.size()).isBetween(1, 9); // some relayed, the rest cut at the bound
        JsonNode last = relayed.events().getLast();
        assertThat(last.path("type").asText()).isEqualTo("error");
        assertThat(last.path("data").path("code").asText()).isEqualTo("turn_timeout");
        assertThat(last.path("data").path("message_ar").asText()).contains("استغرقت الإجابة وقتًا أطول");
        AuditEvent audited = auditLog().events().getLast();
        assertThat(audited.detail()).containsEntry("error_code", "turn_timeout");
        System.out.printf("I3 stream still sending: ended turn_timeout at %d ms (bound %d ms), %d events relayed%n",
                relayed.millis(), BOUND_MS, tokenEvents.size());
    }

    @Test
    void aSilentAgentIsEndedAtTheRelayBoundAndItsConnectionClosed() throws Exception {
        AGENT.enqueue(new MockResponse().setHeader("Content-Type", "text/event-stream")
                .setBody(tokens(1)).setBodyDelay(30, TimeUnit.SECONDS));

        Relayed relayed = chatTurn();

        assertThat(relayed.millis()).isBetween(BOUND_MS, BOUND_MS + 1000);
        JsonNode last = relayed.events().getLast();
        assertThat(last.path("data").path("code").asText()).isEqualTo("turn_timeout");
        // The connection to the agent was closed, not kept: the next request opens a new one.
        RecordedRequest silent = AGENT.takeRequest(5, TimeUnit.SECONDS);
        assertThat(silent).isNotNull();
        AGENT.enqueue(new MockResponse().setHeader("Content-Type", "text/event-stream").setBody(tokens(1)
                + "event: done\nid: 9\ndata: {\"type\":\"done\",\"request_id\":\"r\",\"turn_id\":\"t\",\"seq\":9,"
                + "\"data\":{\"answer\":\"ك\",\"citations\":[],\"usage\":{\"tokens_in\":1,\"tokens_out\":1,\"cost_usd\":0.0},"
                + "\"guard_events\":[],\"state_digest\":{},\"trace_id\":\"4bf92f3577b34da6a3ce929d0e0e4736\"}}\n\n"));
        Relayed next = chatTurn();
        assertThat(next.events().getLast().path("type").asText()).isEqualTo("done");
        RecordedRequest after = AGENT.takeRequest(5, TimeUnit.SECONDS);
        assertThat(after).isNotNull();
        assertThat(after.getSequenceNumber()).isZero();
        System.out.printf("I3 silent agent: ended turn_timeout at %d ms (bound %d ms); the next turn on a new connection%n",
                relayed.millis(), BOUND_MS);
    }

    private static final String OTHER_CONVERSATION_ID = "6f1d2e3c-4b5a-4c7d-8e9f-0a1b2c3d4e5f";
    private static final long OTHER_BOUND_MS = 1000; // another user's turn: a done stream at once

    private static String done() {
        return tokens(1) + "event: done\nid: 9\ndata: {\"type\":\"done\",\"request_id\":\"r\",\"turn_id\":\"t\",\"seq\":9,"
                + "\"data\":{\"answer\":\"ك\",\"citations\":[],\"usage\":{\"tokens_in\":1,\"tokens_out\":1,\"cost_usd\":0.0},"
                + "\"guard_events\":[],\"state_digest\":{},\"trace_id\":\"4bf92f3577b34da6a3ce929d0e0e4736\"}}\n\n";
    }

    private static String otherUser() {
        return token(claims -> claims.subject("user-456").claim("preferred_username", "other"));
    }

    /** User A's turn in a thread of its own once its request reached the agent; then user B's. */
    private Relayed[] withAnotherUser(MockResponse held) throws Exception {
        AGENT.enqueue(held);
        AGENT.enqueue(new MockResponse().setHeader("Content-Type", "text/event-stream").setBody(done()));
        ExecutorService pool = Executors.newSingleThreadExecutor();
        try {
            Future<Relayed> mine = pool.submit(() -> chatTurn());
            assertThat(AGENT.takeRequest(5, TimeUnit.SECONDS)).isNotNull(); // A's request is at the agent
            Relayed other = chatTurn(otherUser(), OTHER_CONVERSATION_ID);
            return new Relayed[] {mine.get(10, TimeUnit.SECONDS), other};
        } finally {
            pool.shutdownNow();
        }
    }

    @Test
    void anotherUsersTurnRunsOnTimeWhileOneStreamIsHeldToItsBound() throws Exception {
        Relayed[] relayed = withAnotherUser(new MockResponse().setHeader("Content-Type", "text/event-stream")
                .setBody(tokens(1)).setBodyDelay(30, TimeUnit.SECONDS));
        Relayed held = relayed[0];
        Relayed other = relayed[1];
        assertThat(other.events().getLast().path("type").asText()).isEqualTo("done");
        assertThat(other.millis()).isLessThan(OTHER_BOUND_MS);
        assertThat(held.millis()).isBetween(BOUND_MS, BOUND_MS + 1000);
        assertThat(held.events().getLast().path("data").path("code").asText()).isEqualTo("turn_timeout");
        System.out.printf("10 relay bound with another user alongside: held stream turn_timeout at %d ms (bound %d ms); "
                + "another user's turn %d ms%n", held.millis(), BOUND_MS, other.millis());
    }

    @Test
    void anAgentThatNeverAnswersItsHeadersIsUnavailableAtTheHeaderBound() throws Exception {
        long headersMs = 1000; // src/test/resources/turn-test.yaml gateway_headers_s
        assertThat(agentClient.relayBound().toMillis()).isEqualTo(BOUND_MS);
        long started = System.nanoTime();
        Relayed[] relayed = withAnotherUser(new MockResponse().setHeader("Content-Type", "text/event-stream")
                .setBody(done()).setHeadersDelay(5, TimeUnit.SECONDS));
        Relayed other = relayed[1];
        long heldMs = (System.nanoTime() - started) / 1_000_000;
        assertThat(other.events().getLast().path("type").asText()).isEqualTo("done");
        assertThat(other.millis()).isLessThan(OTHER_BOUND_MS);
        assertThat(heldMs).isBetween(headersMs, headersMs + 1000);
        AuditEvent audited = auditLog().events().stream()
                .filter(e -> "agent_unavailable".equals(e.detail().get("error_code"))).findFirst().orElseThrow();
        assertThat(audited.detail()).containsEntry("error_code", "agent_unavailable");
        System.out.printf("5 header bound with another user alongside: no headers, agent_unavailable at %d ms "
                + "(bound %d ms); another user's turn %d ms%n", heldMs, headersMs, other.millis());
    }

    private static final long PROFILE_BOUND_MS = 4000; // turn-test.yaml profiles.report: 3 + 1

    private static MockResponse declaring(String deadline) {
        return new MockResponse().setHeader("Content-Type", "text/event-stream")
                .setHeader(AgentClient.TURN_DEADLINE_HEADER, deadline);
    }

    @Test
    void theBoundFollowsTheDeadlineTheAgentDeclaresCappedAtTheLongest() {
        assertThat(agentClient.relayBound("3").toMillis()).isEqualTo(PROFILE_BOUND_MS);
        assertThat(agentClient.relayBound("1").toMillis()).isEqualTo(BOUND_MS);
        assertThat(agentClient.relayBound("300").toMillis()).isEqualTo(PROFILE_BOUND_MS); // capped
        for (String unusable : new String[] {null, "", "abc", "-5", "0", "NaN", "Infinity"}) {
            assertThat(agentClient.relayBound(unusable).toMillis()).as(unusable).isEqualTo(BOUND_MS);
        }
    }

    @Test
    void aStreamWhoseAgentDeclaresTheReportProfileIsNotCutAtTheDefaultBound() throws Exception {
        // Ten events over about 3 s, then done: past the default 2 s bound, inside the profile's 4 s.
        String body = tokens(10) + "event: done\nid: 99\ndata: {\"type\":\"done\",\"request_id\":\"r\","
                + "\"turn_id\":\"t\",\"seq\":99,\"data\":{\"answer\":\"ك\",\"citations\":[],\"usage\":{\"tokens_in\":1,"
                + "\"tokens_out\":1,\"cost_usd\":0.0},\"guard_events\":[],\"state_digest\":{\"output\":\"report\"},"
                + "\"trace_id\":\"4bf92f3577b34da6a3ce929d0e0e4736\"}}\n\n";
        AGENT.enqueue(declaring("3").setBody(body).throttleBody(body.length() / 10, 300, TimeUnit.MILLISECONDS));

        Relayed relayed = chatTurn();

        assertThat(relayed.millis()).isBetween(BOUND_MS, PROFILE_BOUND_MS);
        assertThat(relayed.events().getLast().path("type").asText()).isEqualTo("done");
        assertThat(relayed.events().stream().filter(e -> "token".equals(e.path("type").asText())).count())
                .isEqualTo(10);
        System.out.printf("ADR-0379 report profile: done at %d ms, past the default bound %d ms, inside %d ms%n",
                relayed.millis(), BOUND_MS, PROFILE_BOUND_MS);
    }

    @Test
    void aDeclaredDeadlineAboveTheLongestProfileIsCappedAtIt() throws Exception {
        AGENT.enqueue(declaring("300").setBody(tokens(1)).setBodyDelay(30, TimeUnit.SECONDS));

        Relayed relayed = chatTurn();

        assertThat(relayed.millis()).isBetween(PROFILE_BOUND_MS, PROFILE_BOUND_MS + 1000);
        assertThat(relayed.events().getLast().path("data").path("code").asText()).isEqualTo("turn_timeout");
        System.out.printf("ADR-0379 declared 300 s: ended turn_timeout at %d ms (cap %d ms)%n",
                relayed.millis(), PROFILE_BOUND_MS);
    }

    @Test
    void aMissingOrUnparsableDeclarationKeepsTheDefaultBound() throws Exception {
        AGENT.enqueue(declaring("soon").setBody(tokens(1)).setBodyDelay(30, TimeUnit.SECONDS));

        Relayed relayed = chatTurn();

        assertThat(relayed.millis()).isBetween(BOUND_MS, BOUND_MS + 1000);
        assertThat(relayed.events().getLast().path("data").path("code").asText()).isEqualTo("turn_timeout");
    }

    @Test
    void anyOtherAgentCallIsCutAtTheResponseTimeoutBodyIncluded() throws Exception {
        // The finding: the response timeout is not a header timeout. Headers at once, the body over 3 s.
        // The object closes only at the last byte, so reading it takes the whole 3 s.
        String body = "{\"items\":[]," + " ".repeat(300) + "\"enabled\":true}";
        AGENT.enqueue(new MockResponse().setHeader("Content-Type", "application/json")
                .setBody(body).throttleBody(body.length() / 6, 500, TimeUnit.MILLISECONDS));
        long started = System.nanoTime();
        MvcResult result = mvc.perform(get("/v1/memory").header(HttpHeaders.AUTHORIZATION, "Bearer " + token()))
                .andReturn();
        long millis = (System.nanoTime() - started) / 1_000_000;
        // Cut, not read whole (it would take 3 s and answer 200); how a cut read is mapped is the
        // memory proxy's own matter (today 500 internal_error).
        assertThat(result.getResponse().getStatus()).isGreaterThanOrEqualTo(500);
        assertThat(millis).isBetween(1000L, 2000L);
        System.out.printf("I3 finding: a non-stream agent call whose body outlasts the 1 s response timeout "
                + "is cut at %d ms (HTTP %d)%n", millis, result.getResponse().getStatus());
    }
}
