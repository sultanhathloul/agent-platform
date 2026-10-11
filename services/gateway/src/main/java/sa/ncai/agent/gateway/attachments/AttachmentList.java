package sa.ncai.agent.gateway.attachments;

import io.swagger.v3.oas.annotations.media.Schema;
import java.util.List;

/** A conversation's attachments, oldest first. */
public record AttachmentList(@Schema(requiredMode = Schema.RequiredMode.REQUIRED) List<AttachmentView> attachments) {}
