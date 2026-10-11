package sa.ncai.agent.gateway.attachments;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;

/** The agent's view of an attachment, with the object key the gateway needs for a download. */
@JsonIgnoreProperties(ignoreUnknown = true)
record AgentAttachment(String id, String name, String mime, String objectKey) {}
