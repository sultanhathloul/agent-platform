package sa.ncai.agent.gateway.chat;

import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import java.util.List;
import java.util.Locale;

/**
 * Body of {@code POST /v1/chat/turns}. The message is trimmed before validation. The conversation
 * id is lower-cased, so ownership, audit and the agent's thread all see one spelling (ADR-0050).
 */
public record ChatTurnRequest(
        @NotNull
        @Pattern(regexp = "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED, format = "uuid")
        String conversationId,

        @NotBlank
        @Size(max = MAX_MESSAGE_CHARS)
        @Schema(requiredMode = Schema.RequiredMode.REQUIRED)
        String message,

        @Size(max = 20)
        @Schema(defaultValue = "[]")
        List<@NotNull @Pattern(regexp = "^[a-z0-9_-]{1,64}$") String> selectedKbs,

        @Valid
        @Size(max = 20)
        @Schema(defaultValue = "[]")
        List<@NotNull Attachment> attachments,

        @Pattern(regexp = "^(ar|en)$")
        @Schema(allowableValues = {"ar", "en"}, defaultValue = "ar")
        String locale) {

    public static final int MAX_MESSAGE_CHARS = 8000;

    public ChatTurnRequest {
        conversationId = conversationId == null ? null : conversationId.toLowerCase(Locale.ROOT);
        message = message == null ? null : message.strip();
        selectedKbs = selectedKbs == null ? List.of() : List.copyOf(selectedKbs);
        attachments = attachments == null ? List.of() : List.copyOf(attachments);
        locale = locale == null ? "ar" : locale;
    }
}
