package sa.ncai.agent.gateway.proxy;

import java.io.IOException;

/** Writing to the browser failed (it disconnected); the upstream stream must be abandoned. */
public class ClientGoneException extends RuntimeException {

    public ClientGoneException(IOException cause) {
        super("client disconnected", cause);
    }
}
