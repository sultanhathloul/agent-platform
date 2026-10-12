package sa.ncai.agent.gateway.attachments;

import jakarta.servlet.MultipartConfigElement;
import java.nio.file.Path;
import org.springframework.boot.web.servlet.FilterRegistrationBean;
import org.springframework.boot.web.servlet.MultipartConfigFactory;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.util.unit.DataSize;
import sa.ncai.agent.gateway.GatewayProperties;
import sa.ncai.agent.gateway.api.ErrorResponses;
import sa.ncai.agent.gateway.audit.AuditTrail;

/**
 * Loads the upload limits once at startup; a bad docs config fails the gateway. The multipart caps
 * come from the same limits (owner decision R3): one file a little over {@code max_file_bytes}
 * still reaches the controller, which refuses it with an audit record; a request over
 * {@link DocsLimits#maxRequestBytes()} is refused by {@link AttachmentGate} before it is read.
 */
@Configuration(proxyBeanMethods = false)
class AttachmentsConfig {

    @Bean
    DocsLimits docsLimits(GatewayProperties properties) {
        return DocsLimits.load(Path.of(properties.docs().config()));
    }

    @Bean
    MultipartConfigElement multipartConfigElement(DocsLimits limits) {
        MultipartConfigFactory factory = new MultipartConfigFactory();
        factory.setMaxFileSize(DataSize.ofBytes(limits.maxFileBytes() + DocsLimits.ENVELOPE_BYTES));
        factory.setMaxRequestSize(DataSize.ofBytes(limits.maxRequestBytes()));
        factory.setFileSizeThreshold(DataSize.ofMegabytes(1));
        return factory.createMultipartConfig();
    }

    @Bean
    UploadRateLimiter uploadRateLimiter(DocsLimits limits) {
        return new UploadRateLimiter(limits.uploadsPerMinute(), java.time.Clock.systemUTC());
    }

    @Bean
    AttachmentGate attachmentGate(
            DocsLimits limits, UploadRateLimiter rateLimiter, AuditTrail audit, ErrorResponses errors) {
        return new AttachmentGate(limits, rateLimiter, audit, errors);
    }

    /** The gate runs inside the security chain, after authentication; never as a plain servlet filter too. */
    @Bean
    FilterRegistrationBean<AttachmentGate> attachmentGateRegistration(AttachmentGate gate) {
        FilterRegistrationBean<AttachmentGate> registration = new FilterRegistrationBean<>(gate);
        registration.setEnabled(false);
        return registration;
    }
}
