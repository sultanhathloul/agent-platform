package sa.ncai.agent.gateway.skills;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import io.swagger.v3.oas.annotations.media.Schema;
import java.util.List;

/** The skills the caller may use (phase 06): slug, name and description in Arabic and English. */
@JsonIgnoreProperties(ignoreUnknown = true)
public record SkillList(@Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<SkillView> skills) {

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record SkillView(
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String slug,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) Label name,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) Label description) {}

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record Label(
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String ar,
            @Schema(requiredMode = Schema.RequiredMode.REQUIRED) String en) {}
}
