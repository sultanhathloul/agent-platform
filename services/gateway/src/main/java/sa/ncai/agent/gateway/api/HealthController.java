package sa.ncai.agent.gateway.api;

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.security.SecurityRequirements;
import io.swagger.v3.oas.annotations.tags.Tag;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@Tag(name = "health")
public class HealthController {

    public record HealthResponse(String status) {}

    @GetMapping(path = "/v1/health", produces = MediaType.APPLICATION_JSON_VALUE)
    @Operation(summary = "Liveness of the gateway itself")
    @SecurityRequirements
    public HealthResponse health() {
        return new HealthResponse("ok");
    }
}
