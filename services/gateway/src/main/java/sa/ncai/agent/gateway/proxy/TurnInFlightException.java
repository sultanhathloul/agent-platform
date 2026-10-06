package sa.ncai.agent.gateway.proxy;

/** The agent refused a turn because another turn holds the conversation (409 turn_in_flight, D6). */
public class TurnInFlightException extends RuntimeException {

    public TurnInFlightException() {
        super("another turn holds the conversation");
    }
}
