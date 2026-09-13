package sa.ncai.agent.gateway.api;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;

/**
 * Writes the JSON error envelope straight to the servlet response. Bypassing content
 * negotiation keeps errors JSON even when the client asked for {@code text/event-stream}.
 */
@Component
public class ErrorResponses {

    private static final Logger log = LoggerFactory.getLogger(ErrorResponses.class);

    private final ObjectMapper mapper;
    private final ErrorMessages messages;

    public ErrorResponses(ObjectMapper mapper, ErrorMessages messages) {
        this.mapper = mapper;
        this.messages = messages;
    }

    public void write(HttpServletRequest request, HttpServletResponse response, ErrorCode code) throws IOException {
        write(request, response, code, code.status());
    }

    public void write(HttpServletRequest request, HttpServletResponse response, ErrorCode code, int status)
            throws IOException {
        if (response.isCommitted()) {
            log.warn("response already committed; cannot send error {}", code.code());
            return;
        }
        response.resetBuffer();
        response.setStatus(status);
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        response.setCharacterEncoding(StandardCharsets.UTF_8.name());
        ApiErrorEnvelope body = new ApiErrorEnvelope(RequestIdFilter.requestId(request), messages.error(code));
        response.getOutputStream().write(mapper.writeValueAsBytes(body));
        response.flushBuffer();
    }
}
