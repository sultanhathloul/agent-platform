package sa.ncai.agent.gateway.api;

import com.fasterxml.jackson.databind.ObjectMapper;
import io.swagger.v3.core.jackson.ModelResolver;
import io.swagger.v3.oas.models.Components;
import io.swagger.v3.oas.models.OpenAPI;
import io.swagger.v3.oas.models.info.Info;
import io.swagger.v3.oas.models.security.SecurityRequirement;
import io.swagger.v3.oas.models.security.SecurityScheme;
import io.swagger.v3.oas.models.servers.Server;
import java.util.List;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/** OpenAPI 3.1 description of the public API (README §6.3); committed as docs/openapi/gateway.json. */
@Configuration(proxyBeanMethods = false)
class OpenApiConfig {

    /** Bump on any contract change; a spec diff without a bump fails CI (phase 09). */
    static final String API_VERSION = "1.0.0";

    @Bean
    OpenAPI gatewayOpenApi() {
        return new OpenAPI()
                .info(new Info().title("Agent Platform Gateway API").version(API_VERSION))
                .servers(List.of(new Server().url("/").description("Gateway")))
                .components(new Components().addSecuritySchemes("bearer",
                        new SecurityScheme().type(SecurityScheme.Type.HTTP).scheme("bearer").bearerFormat("JWT")))
                .addSecurityItem(new SecurityRequirement().addList("bearer"));
    }

    /** Makes the spec use the application's ObjectMapper, so schemas are snake_case like the wire format. */
    @Bean
    ModelResolver modelResolver(ObjectMapper objectMapper) {
        return new ModelResolver(objectMapper);
    }
}
