package sa.ncai.agent.gateway.proxy;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.MissingNode;

/**
 * How a relayed turn ended. {@code data} is the terminal event's {@code data} object
 * ({@code done} or {@code error}); missing for {@link Kind#INTERRUPTED} and {@link Kind#TIMED_OUT}. {@code turnId} is the
 * terminal event's {@code turn_id}, null when absent (ADR-0048).
 */
public record RelayOutcome(Kind kind, JsonNode data, String turnId) {

    public enum Kind {
        /** The agent sent {@code done}. */
        DONE,
        /** The agent sent {@code error}. */
        ERROR,
        /** The stream broke or ended without a terminal event; the gateway appended an error event. */
        INTERRUPTED,
        /** The stream reached the relay's bound without a terminal event; the gateway appended turn_timeout. */
        TIMED_OUT
    }

    static RelayOutcome interrupted() {
        return new RelayOutcome(Kind.INTERRUPTED, MissingNode.getInstance(), null);
    }

    static RelayOutcome timedOut() {
        return new RelayOutcome(Kind.TIMED_OUT, MissingNode.getInstance(), null);
    }
}
