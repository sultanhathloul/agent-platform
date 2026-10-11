package sa.ncai.agent.gateway.attachments;

import sa.ncai.agent.gateway.proxy.AgentTurnRequest;

/** Body of the agent's {@code POST /v1/conversations/{id}/attachments} (ADR-0129). */
record AttachmentRegistration(AgentTurnRequest.Principal principal, String name, String mime, long size, String objectKey) {}
