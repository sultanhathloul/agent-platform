package sa.ncai.agent.gateway.attachments;

import java.util.List;

/** A conversation's attachments, oldest first. */
public record AttachmentList(List<AttachmentView> attachments) {}
