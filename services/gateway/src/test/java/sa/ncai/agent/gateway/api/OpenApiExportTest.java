package sa.ncai.agent.gateway.api;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.MethodOrderer;
import org.junit.jupiter.api.Order;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestMethodOrder;
import sa.ncai.agent.gateway.GatewayTestSupport;

/**
 * Checks the published contract and writes it to build/openapi/gateway.json; that file is
 * copied to docs/openapi/gateway.json (README §6.3). A second test fails when the committed
 * copy no longer matches (ADR-0044).
 */
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
class OpenApiExportTest extends GatewayTestSupport {

    static final Path OUTPUT = Path.of("build", "openapi", "gateway.json");

    /** The committed spec: docs/openapi/gateway.json in a checkout, GATEWAY_OPENAPI_DOC in the test image. */
    static final Path COMMITTED =
            Path.of(System.getenv().getOrDefault("GATEWAY_OPENAPI_DOC", "../../docs/openapi/gateway.json"));

    @Test
    @Order(1)
    void exportsSnakeCaseSpec() throws Exception {
        String json = mvc.perform(get("/v3/api-docs"))
                .andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString(StandardCharsets.UTF_8);
        ObjectMapper plain = new ObjectMapper().enable(SerializationFeature.INDENT_OUTPUT);
        JsonNode spec = plain.readTree(json);

        assertThat(spec.path("openapi").asText()).startsWith("3.1");
        assertThat(spec.path("info").path("version").asText()).isEqualTo(OpenApiConfig.API_VERSION);
        assertThat(spec.path("paths").has("/v1/me")).isTrue();
        assertThat(spec.path("paths").has("/v1/chat/turns")).isTrue();

        JsonNode schemas = spec.path("components").path("schemas");
        assertThat(schemas.path("MeResponse").path("properties").has("user_id")).isTrue();
        assertThat(schemas.path("MeResponse").path("properties").has("is_admin")).isTrue();
        assertThat(schemas.path("ChatTurnRequest").path("properties").has("conversation_id")).isTrue();
        assertThat(schemas.path("ChatTurnRequest").path("properties").has("selected_kbs")).isTrue();
        assertThat(schemas.path("ApiError").path("properties").has("message_ar")).isTrue();
        assertThat(schemas.has("ApiEnvelopeMeResponse")).isTrue();
        assertThat(schemas.has("ApiErrorEnvelope")).isTrue();

        Files.createDirectories(OUTPUT.getParent());
        Files.writeString(OUTPUT, plain.writeValueAsString(spec) + "\n", StandardCharsets.UTF_8);
    }

    @Test
    @Order(2)
    void committedSpecMatchesTheBuildOutput() throws Exception {
        assertThat(OUTPUT).as("build output written by exportsSnakeCaseSpec").isRegularFile();
        assertThat(COMMITTED).as("committed spec (set GATEWAY_OPENAPI_DOC)").isRegularFile();
        ObjectMapper plain = new ObjectMapper();
        assertThat(plain.readTree(COMMITTED.toFile()))
                .as("docs/openapi/gateway.json is stale: run `make openapi` and bump OpenApiConfig.API_VERSION")
                .isEqualTo(plain.readTree(OUTPUT.toFile()));
    }
}
