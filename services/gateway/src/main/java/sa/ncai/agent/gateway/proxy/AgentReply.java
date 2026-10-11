package sa.ncai.agent.gateway.proxy;

/** An agent response read whole: its status and body bytes (admin endpoints). */
public record AgentReply(int status, byte[] body) {

    public boolean ok() {
        return status >= 200 && status < 300;
    }
}
