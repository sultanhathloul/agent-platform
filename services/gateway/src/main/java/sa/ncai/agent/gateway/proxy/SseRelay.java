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
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import sa.ncai.agent.gateway.api.ApiError;
import sa.ncai.agent.gateway.api.ErrorCode;
import sa.ncai.agent.gateway.api.ErrorMessages;

/**
 * Copies the agent's SSE stream to the browser byte for byte (README §6.1.6), flushing at
 * every event boundary. It watches for the terminal {@code done}/{@code error} event for the
 * audit record. If the stream breaks or ends without one, it appends a single
 * {@code agent_stream_interrupted} error event in the agent's envelope format.
 */
@Component
public class SseRelay {

    private static final Logger log = LoggerFactory.getLogger(SseRelay.class);

    private final ObjectMapper mapper;
    private final ErrorMessages messages;

    public SseRelay(ObjectMapper mapper, ErrorMessages messages) {
        this.mapper = mapper;
        this.messages = messages;
    }

    /**
     * @throws ClientGoneException when writing to {@code downstream} fails
     */
    public RelayOutcome relay(InputStream upstream, OutputStream downstream, String requestId) {
        InputStream in = new BufferedInputStream(upstream, 8192);
        ByteArrayOutputStream line = new ByteArrayOutputStream(512);
        String eventType = null;
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
            write(downstream, bytes);

            String text = stripLineEnding(new String(bytes, StandardCharsets.UTF_8));
            if (text.isEmpty()) {
                flush(downstream);
                RelayOutcome outcome = terminalOutcome(eventType, data);
                if (outcome != null) {
                    terminal = outcome;
                }
                eventType = null;
                data.setLength(0);
            } else if (text.startsWith("event:")) {
                eventType = fieldValue(text, "event:".length());
            } else if (text.startsWith("data:")) {
                if (!data.isEmpty()) {
                    data.append('\n');
                }
                data.append(fieldValue(text, "data:".length()));
            }
        }

        if (line.size() > 0) {
            write(downstream, line.toByteArray());
        }
        if (terminal != null) {
            flush(downstream);
            return terminal;
        }
        write(downstream, interruptedEvent(requestId));
        flush(downstream);
        return RelayOutcome.interrupted();
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
        JsonNode payload;
        try {
            payload = mapper.readTree(data.toString()).path("data");
        } catch (JsonProcessingException e) {
            log.warn("unparseable {} event from agent", eventType);
            payload = mapper.missingNode();
        }
        return new RelayOutcome(kind, payload);
    }

    private byte[] interruptedEvent(String requestId) {
        ApiError error = messages.error(ErrorCode.AGENT_STREAM_INTERRUPTED);
        ObjectNode envelope = mapper.createObjectNode();
        envelope.put("type", "error");
        envelope.put("request_id", requestId);
        envelope.putNull("turn_id");
        envelope.put("seq", -1);
        ObjectNode payload = envelope.putObject("data");
        payload.put("code", error.code());
        payload.put("message_ar", error.messageAr());
        payload.put("message_en", error.messageEn());
        payload.put("retryable", true);
        try {
            return ("event: error\ndata: " + mapper.writeValueAsString(envelope) + "\n\n")
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
