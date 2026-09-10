package sa.ncai.agent.gateway.proxy;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.MissingNode;

/**
 * How a relayed turn ended. {@code data} is the terminal event's {@code data} object
 * ({@code done} or {@code error}); missing for {@link Kind#INTERRUPTED}.
 */
public record RelayOutcome(Kind kind, JsonNode data) {

    public enum Kind {
        /** The agent sent {@code done}. */
        DONE,
        /** The agent sent {@code error}. */
        ERROR,
        /** The stream broke or ended without a terminal event; the gateway appended an error event. */
        INTERRUPTED
    }

    static RelayOutcome interrupted() {
        return new RelayOutcome(Kind.INTERRUPTED, MissingNode.getInstance());
    }
}
