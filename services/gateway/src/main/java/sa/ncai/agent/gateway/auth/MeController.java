package sa.ncai.agent.gateway.auth;

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.Parameter;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.MediaType;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
import sa.ncai.agent.gateway.api.ApiEnvelope;
import sa.ncai.agent.gateway.api.RequestIdFilter;

@RestController
@Tag(name = "identity")
public class MeController {

    @GetMapping(path = "/v1/me", produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(summary = "Current user, tenant and permissions")
    public ApiEnvelope<MeResponse> me(
            @Parameter(hidden = true) @AuthenticationPrincipal Jwt jwt, HttpServletRequest request) {
        return ApiEnvelope.ok(RequestIdFilter.requestId(request), MeResponse.of(GatewayPrincipal.from(jwt)));
    }
}
