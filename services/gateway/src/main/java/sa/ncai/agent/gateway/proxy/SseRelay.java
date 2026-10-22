package sa.ncai.agent.gateway.proxy;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.io.BufferedInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.function.Predicate;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import sa.ncai.agent.gateway.api.ApiError;
import sa.ncai.agent.gateway.api.ErrorCode;
import sa.ncai.agent.gateway.api.ErrorMessages;

/**
 * Copies the agent's SSE stream to the browser byte for byte (README §6.1.6), one event block at
 * a time, flushing at every event boundary. Before the terminal {@code done}/{@code error} event
 * leaves, {@code commit} writes the audit record from it; the terminal event's {@code guard_audit}
 * (every guard evaluation in full, for that record only) and {@code tool_audit} (every tool call,
 * content-free) are then removed, so a client never receives a checker's detail (ADR-0116,
 * ADR-0219). When the audit record fails, the terminal event is replaced
 * by {@code retract} and an {@code audit_unavailable} error, so no turn completes without its
 * audit record (ADR-0042). If the stream breaks or ends without a terminal event, a single
 * {@code agent_stream_interrupted} error event is appended, also after {@code commit}; when that
 * happens at or past the relay's bound ({@code deadlineNanos}: the agent client closes the stream
 * there, ADR-0256), the event is {@code turn_timeout} instead. An event
 * block cut off before its closing blank line is discarded, never forwarded: a client receives
 * only complete events, each one as this relay passed it (ADR-0118).
 */
@Component
public class SseRelay {

    private static final Logger log = LoggerFactory.getLogger(SseRelay.class);
    /** The terminal event's audit-only list of guard evaluations (ADR-0116). */
    static final String GUARD_AUDIT = "guard_audit";
    /** The terminal event's audit-only list of tool calls, content-free (phase 05, G8, ADR-0219). */
    static final String TOOL_AUDIT = "tool_audit";

    private final ObjectMapper mapper;
    private final ErrorMessages messages;

    public SseRelay(ObjectMapper mapper, ErrorMessages messages) {
        this.mapper = mapper;
        this.messages = messages;
    }

    /**
     * @param deadlineNanos the relay's bound on {@link System#nanoTime()}'s clock: a stream that ends
     *                      without a terminal event at or past it ended by the bound (turn_timeout)
     * @param commit writes the audit record for the outcome; returns false when it could not
     * @throws ClientGoneException when writing to {@code downstream} fails
     */
    public RelayOutcome relay(
            InputStream upstream, OutputStream downstream, String requestId, long deadlineNanos,
            Predicate<RelayOutcome> commit) {
        InputStream in = new BufferedInputStream(upstream, 8192);
        ByteArrayOutputStream line = new ByteArrayOutputStream(512);
        ByteArrayOutputStream block = new ByteArrayOutputStream(1024);
        String eventType = null;
        String eventId = null;
        StringBuilder data = new StringBuilder();
        RelayOutcome terminal = null;

        while (true) {
            int b;
            try {
                b = in.read();
            } catch (IOException e) {
                log.warn("agent stream broke: {}", e.toString());
                break;
            }
            if (b == -1) {
                break;
            }
            line.write(b);
            if (b != '\n') {
                continue;
            }
            byte[] bytes = line.toByteArray();
            line.reset();
            block.writeBytes(bytes);

            String text = stripLineEnding(new String(bytes, StandardCharsets.UTF_8));
            if (text.isEmpty()) {
                RelayOutcome outcome = terminal == null ? terminalOutcome(eventType, data) : null;
                if (outcome != null) {
                    terminal = outcome;
                    write(downstream, commit.test(outcome)
                            ? forClient(eventType, eventId, data, block.toByteArray())
                            : auditUnavailable(requestId));
                } else {
                    write(downstream, block.toByteArray());
                }
                flush(downstream);
                block.reset();
                eventType = null;
                eventId = null;
                data.setLength(0);
            } else if (text.startsWith("event:")) {
                eventType = fieldValue(text, "event:".length());
            } else if (text.startsWith("id:")) {
                eventId = fieldValue(text, "id:".length());
            } else if (text.startsWith("data:")) {
                if (!data.isEmpty()) {
                    data.append('\n');
                }
                data.append(fieldValue(text, "data:".length()));
            }
        }

        // An unfinished event block is discarded: it never went through the checks above, and a
        // terminal event cut off here would still carry its guard_audit (ADR-0118).
        if (block.size() > 0 || line.size() > 0) {
            log.warn("agent stream ended inside an event; {} bytes discarded", block.size() + line.size());
        }
        if (terminal != null) {
            flush(downstream);
            return terminal;
        }
        boolean timedOut = System.nanoTime() - deadlineNanos >= 0;
        RelayOutcome ended = timedOut ? RelayOutcome.timedOut() : RelayOutcome.interrupted();
        write(downstream, commit.test(ended)
                ? errorEvent(requestId, timedOut ? ErrorCode.TURN_TIMEOUT : ErrorCode.AGENT_STREAM_INTERRUPTED)
                : auditUnavailable(requestId));
        flush(downstream);
        return ended;
    }

    private RelayOutcome terminalOutcome(String eventType, StringBuilder data) {
        RelayOutcome.Kind kind;
        if ("done".equals(eventType)) {
            kind = RelayOutcome.Kind.DONE;
        } else if ("error".equals(eventType)) {
            kind = RelayOutcome.Kind.ERROR;
        } else {
            return null;
        }
        JsonNode envelope;
        try {
            envelope = mapper.readTree(data.toString());
        } catch (JsonProcessingException e) {
            log.warn("unparseable {} event from agent", eventType);
            envelope = mapper.missingNode();
        }
        JsonNode turnId = envelope.path("turn_id");
        return new RelayOutcome(kind, envelope.path("data"), turnId.isTextual() ? turnId.asText() : null);
    }

    /**
     * The terminal event as a client gets it: without {@code data.guard_audit} and
     * {@code data.tool_audit}, which only the audit record reads (ADR-0116, ADR-0219). An event
     * without either field leaves byte for byte.
     */
    private byte[] forClient(String eventType, String eventId, StringBuilder data, byte[] block) {
        JsonNode envelope;
        try {
            envelope = mapper.readTree(data.toString());
        } catch (JsonProcessingException e) {
            return block;
        }
        if (!(envelope.path("data") instanceof ObjectNode payload)
                || !(payload.has(GUARD_AUDIT) || payload.has(TOOL_AUDIT))) {
            return block;
        }
        payload.remove(GUARD_AUDIT);
        payload.remove(TOOL_AUDIT);
        try {
            String id = eventId == null ? "" : "id: " + eventId + "\n";
            return ("event: " + eventType + "\n" + id + "data: " + mapper.writeValueAsString(envelope) + "\n\n")
                    .getBytes(StandardCharsets.UTF_8);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException(e);
        }
    }

    /** Withdraws any streamed text, then reports that the turn could not be audited. */
    private byte[] auditUnavailable(String requestId) {
        ObjectNode retract = envelope("retract", requestId);
        retract.putObject("data").put("reason", ErrorCode.AUDIT_UNAVAILABLE.code());
        ByteArrayOutputStream out = new ByteArrayOutputStream(512);
        out.writeBytes(sse("retract", retract));
        out.writeBytes(errorEvent(requestId, ErrorCode.AUDIT_UNAVAILABLE));
        return out.toByteArray();
    }

    private byte[] errorEvent(String requestId, ErrorCode code) {
        ApiError error = messages.error(code);
        ObjectNode envelope = envelope("error", requestId);
        ObjectNode payload = envelope.putObject("data");
        payload.put("code", error.code());
        payload.put("message_ar", error.messageAr());
        payload.put("message_en", error.messageEn());
        payload.put("retryable", true);
        return sse("error", envelope);
    }

    /** The agent's event envelope for an event the gateway injects ({@code seq: -1}). */
    private ObjectNode envelope(String type, String requestId) {
        ObjectNode envelope = mapper.createObjectNode();
        envelope.put("type", type);
        envelope.put("request_id", requestId);
        envelope.putNull("turn_id");
        envelope.put("seq", -1);
        return envelope;
    }

    private byte[] sse(String type, ObjectNode envelope) {
        try {
            return ("event: " + type + "\ndata: " + mapper.writeValueAsString(envelope) + "\n\n")
                    .getBytes(StandardCharsets.UTF_8);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException(e);
        }
    }

    private static String stripLineEnding(String text) {
        int end = text.length();
        while (end > 0 && (text.charAt(end - 1) == '\n' || text.charAt(end - 1) == '\r')) {
            end--;
        }
        return text.substring(0, end);
    }

    /** SSE: the value follows the colon, minus one optional leading space. */
    private static String fieldValue(String text, int start) {
        return text.startsWith(" ", start) ? text.substring(start + 1) : text.substring(start);
    }

    private static void write(OutputStream out, byte[] bytes) {
        try {
            out.write(bytes);
        } catch (IOException e) {
            throw new ClientGoneException(e);
        }
    }

    private static void flush(OutputStream out) {
        try {
            out.flush();
        } catch (IOException e) {
            throw new ClientGoneException(e);
        }
    }
}
