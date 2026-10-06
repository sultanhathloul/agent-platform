package sa.ncai.agent.gateway.admin;

import io.swagger.v3.oas.annotations.media.Schema;
import java.util.List;

/** The realm's policy roles ({@code policy:*}), which role-scoped policies name (ADR-0107). */
public record PolicyRoles(@Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<String> roles) {}
