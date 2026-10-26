package sa.ncai.agent.gateway.skills;

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
 * The skills the caller may use, for the composer's picker (phase 06, ADR-0292, ADR-0301). The
 * agent filters its in-memory registry by the permissions the gateway sends from the verified
 * token; the labels come from each SKILL.md, so a new skill needs no gateway change. A read of
 * labels only, like {@code /v1/me}: not audited.
 */
@RestController
@Tag(name = "skills")
public class SkillController {

    private static final Logger log = LoggerFactory.getLogger(SkillController.class);

    private final AgentClient agent;
    private final ErrorResponses errors;
    private final ObjectMapper mapper;

    public SkillController(AgentClient agent, ErrorResponses errors, ObjectMapper mapper) {
        this.agent = agent;
        this.errors = errors;
        this.mapper = mapper;
    }

    @GetMapping(path = "/v1/skills", produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(
            summary = "The skills the caller may pick",
            responses = {
                @ApiResponse(responseCode = "200", description = "The caller's skills, by slug"),
                @ApiResponse(responseCode = "401", description = "unauthorized", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class))),
                @ApiResponse(responseCode = "502", description = "agent_unavailable", content = @Content(
                        mediaType = MediaType.APPLICATION_JSON_VALUE,
                        schema = @Schema(implementation = ApiErrorEnvelope.class)))
            })
    public ResponseEntity<ApiEnvelope<SkillList>> list(
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt,
            HttpServletRequest request,
            HttpServletResponse response) throws IOException {
        GatewayPrincipal caller = GatewayPrincipal.from(jwt);
        String requestId = RequestIdFilter.requestId(request);
        AgentReply reply;
        try {
            reply = agent.listSkills(caller, requestId);
        } catch (AgentUnavailableException e) {
            log.warn("{}", e.getMessage());
            errors.write(request, response, ErrorCode.AGENT_UNAVAILABLE);
            return null;
        }
        if (reply.status() != 200) {
            log.warn("agent answered {} to /v1/skills", reply.status());
            errors.write(request, response, ErrorCode.AGENT_UNAVAILABLE);
            return null;
        }
        return ResponseEntity.ok(ApiEnvelope.ok(requestId, mapper.readValue(reply.body(), SkillList.class)));
    }
}
