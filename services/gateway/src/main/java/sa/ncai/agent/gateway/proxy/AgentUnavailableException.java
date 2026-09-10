package sa.ncai.agent.gateway.proxy;

/** The agent could not be reached or refused the turn before any event was streamed. */
public class AgentUnavailableException extends RuntimeException {

    public AgentUnavailableException(String message) {
        super(message);
    }

    public AgentUnavailableException(String message, Throwable cause) {
        super(message, cause);
    }
}
