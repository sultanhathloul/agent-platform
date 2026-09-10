package sa.ncai.agent.gateway.chat;

import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import java.util.List;

/** Body of {@code POST /v1/chat/turns}. The message is trimmed before validation. */
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
        message = message == null ? null : message.strip();
        selectedKbs = selectedKbs == null ? List.of() : List.copyOf(selectedKbs);
        attachments = attachments == null ? List.of() : List.copyOf(attachments);
        locale = locale == null ? "ar" : locale;
    }
}
