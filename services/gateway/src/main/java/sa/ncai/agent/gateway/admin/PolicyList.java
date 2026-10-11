package sa.ncai.agent.gateway.admin;

import io.swagger.v3.oas.annotations.media.Schema;
import java.util.List;

public record PolicyList(@Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<PolicyView> policies) {}
