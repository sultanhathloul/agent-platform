package sa.ncai.agent.gateway.chat;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.PositiveOrZero;

/** Attachment metadata; the gateway forwards it to the agent unchanged. */
public record Attachment(@NotBlank String id, @NotBlank String name, @NotBlank String mime, @PositiveOrZero long size) {}
