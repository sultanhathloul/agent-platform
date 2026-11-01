package sa.ncai.agent.gateway.chat;

import io.swagger.v3.oas.annotations.media.ArraySchema;
import io.swagger.v3.oas.annotations.media.Schema;
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

        // The default belongs to the array, not to its items (owner decision C4: no "[]" item default).
        @Size(max = 20)
        @ArraySchema(arraySchema = @Schema(defaultValue = "[]"), schema = @Schema(pattern = "^[a-z0-9_-]{1,64}$"))
        List<@NotNull @Pattern(regexp = "^[a-z0-9_-]{1,64}$") String> selectedKbs,

        @Size(max = 10)
        @ArraySchema(
                arraySchema = @Schema(defaultValue = "[]",
                        description = "Attachments sent with this turn, uploaded first (A1, ADR-0129)."),
                schema = @Schema(pattern = UUID))
        List<@NotNull @Pattern(regexp = UUID) String> attachmentIds,

        @Pattern(regexp = "^(ar|en)$")
        @Schema(allowableValues = {"ar", "en"}, defaultValue = "ar")
        String locale,

        @Pattern(regexp = SKILL)
        @Schema(nullable = true, pattern = SKILL,
                description = "The skill picked in the composer (phase 06, ADR-0292); absent: chosen automatically. "
                        + "A skill the caller holds no skill:{slug} role for is 403 skill_forbidden.")
        String skill,

        @Pattern(regexp = OUTPUT)
        @Schema(nullable = true, allowableValues = {"text", "report"},
                description = "The output form picked in the composer (reports PR, D3); absent: chosen automatically. "
                        + "`report` writes an interactive HTML report.")
        String output) {

    public static final int MAX_MESSAGE_CHARS = 8000;
    static final String SKILL = "^[a-z][a-z0-9_-]{0,63}$";
    static final String OUTPUT = "^(text|report)$";
    static final String UUID = "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$";

    public ChatTurnRequest {
        conversationId = conversationId == null ? null : conversationId.toLowerCase(Locale.ROOT);
        message = message == null ? null : message.strip();
        selectedKbs = selectedKbs == null ? List.of() : List.copyOf(selectedKbs);
        attachmentIds = attachmentIds == null
                ? List.of()
                : attachmentIds.stream().map(id -> id.toLowerCase(Locale.ROOT)).toList();
        locale = locale == null ? "ar" : locale;
    }
}
