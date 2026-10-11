package sa.ncai.agent.gateway.admin;

import io.swagger.v3.oas.annotations.media.Schema;
import java.util.List;

/** A policy's change log, newest first (ADR-0108). */
public record PolicyChanges(@Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<PolicyChange> changes) {}
