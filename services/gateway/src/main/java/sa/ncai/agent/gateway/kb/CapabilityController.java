package sa.ncai.agent.gateway.kb;

import com.fasterxml.jackson.databind.ObjectMapper;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.Parameter;
import io.swagger.v3.oas.annotations.media.Content;
import io.swagger.v3.oas.annotations.media.Schema;
import io.swagger.v3.oas.annotations.responses.ApiResponse;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
import sa.ncai.agent.gateway.api.ApiEnvelope;
import sa.ncai.agent.gateway.api.ApiErrorEnvelope;
import sa.ncai.agent.gateway.api.ErrorCode;
import sa.ncai.agent.gateway.api.ErrorResponses;
import sa.ncai.agent.gateway.api.RequestIdFilter;
import sa.ncai.agent.gateway.auth.GatewayPrincipal;
import sa.ncai.agent.gateway.proxy.AgentClient;
import sa.ncai.agent.gateway.proxy.AgentReply;
import sa.ncai.agent.gateway.proxy.AgentUnavailableException;

/**
 * What the composer renders (README §13, phase 08 I3): the caller's capabilities and the knowledge
 * bases it may read, filtered by the agent from the permissions the gateway sends from the verified
 * token; a KB the caller lacks {@code kb:<slug>:read} for, another tenant's or a deleted one is never
 * listed (I7). A read of labels, like {@code /v1/me} and {@code /v1/skills}: not audited.
 */
@RestController
@Tag(name = "capabilities")
public class CapabilityController {

    private static final Logger log = LoggerFactory.getLogger(CapabilityController.class);

    private final AgentClient agent;
    private final ErrorResponses errors;
    private final ObjectMapper mapper;

    public CapabilityController(AgentClient agent, ErrorResponses errors, ObjectMapper mapper) {
        this.agent = agent;
        this.errors = errors;
        this.mapper = mapper;
    }

    @GetMapping(path = "/v1/capabilities", produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(
            summary = "The caller's capabilities and the knowledge bases it may toggle",
            responses = {
                @ApiResponse(responseCode = "200", description = "Capabilities and readable knowledge bases"),
                @ApiResponse(responseCode = "401", description = "unauthorized", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "502", description = "agent_unavailable", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class)))
            })
    public ResponseEntity<ApiEnvelope<KbDtos.CapabilityList>> list(
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        GatewayPrincipal caller = GatewayPrincipal.from(jwt);
        String requestId = RequestIdFilter.requestId(request);
        AgentReply reply;
        try {
            reply = agent.listCapabilities(caller, requestId);
        } catch (AgentUnavailableException e) {
            log.warn("{}", e.getMessage());
            errors.write(request, response, ErrorCode.AGENT_UNAVAILABLE);
            return null;
        }
        if (reply.status() != 200) {
            log.warn("agent answered {} to /v1/capabilities", reply.status());
            errors.write(request, response, ErrorCode.AGENT_UNAVAILABLE);
            return null;
        }
        return ResponseEntity.ok(ApiEnvelope.ok(requestId, mapper.readValue(reply.body(), KbDtos.CapabilityList.class)));
    }
}
