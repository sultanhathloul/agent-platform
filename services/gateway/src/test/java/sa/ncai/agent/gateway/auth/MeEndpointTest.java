package sa.ncai.agent.gateway.auth;

import static org.hamcrest.Matchers.contains;
import static org.hamcrest.Matchers.not;
import static org.hamcrest.Matchers.emptyOrNullString;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.nimbusds.jwt.JWTClaimsSet;
import java.time.Instant;
import java.util.Date;
import java.util.List;
import java.util.Map;
import java.util.function.Consumer;
import java.util.stream.Stream;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.springframework.http.HttpHeaders;
import org.springframework.test.web.servlet.ResultActions;
import sa.ncai.agent.gateway.GatewayTestSupport;

class MeEndpointTest extends GatewayTestSupport {

    @Test
    void validTokenYieldsPrincipalWithOnlyCapabilityAndKbPermissions() throws Exception {
        mvc.perform(get("/v1/me").header(HttpHeaders.AUTHORIZATION, bearer(token())))
                .andExpect(status().isOk())
                .andExpect(header().string("X-Request-Id", not(emptyOrNullString())))
                .andExpect(jsonPath("$.request_id").isNotEmpty())
                .andExpect(jsonPath("$.data.user_id").value("user-123"))
                .andExpect(jsonPath("$.data.tenant_id").value("tenant-a"))
                .andExpect(jsonPath("$.data.permissions", contains("cap:general", "kb:moe:read")))
                .andExpect(jsonPath("$.data.display_name").value("سارة المحللة"))
                .andExpect(jsonPath("$.data.is_admin").value(false))
                .andExpect(jsonPath("$.error").doesNotExist());
    }

    @Test
    void missingTenantDefaultsToDefault() throws Exception {
        me(claims -> claims.claim("tenant_id", null))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.tenant_id").value("default"));
    }

    @Test
    void blankTenantDefaultsToDefault() throws Exception {
        me(claims -> claims.claim("tenant_id", "  "))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.tenant_id").value("default"));
    }

    @Test
    void displayNameFallsBackToUsername() throws Exception {
        me(claims -> claims.claim("name", null))
                .andExpect(jsonPath("$.data.display_name").value("analyst"));
    }

    @Test
    void adminRoleSetsFlagButIsNotAPermission() throws Exception {
        me(claims -> claims.claim("realm_access", Map.of("roles", List.of("user", "admin", "cap:general"))))
                .andExpect(jsonPath("$.data.is_admin").value(true))
                .andExpect(jsonPath("$.data.permissions", contains("cap:general")));
    }

    @Test
    void legacyAndMalformedRolesAreNotPermissions() throws Exception {
        me(claims -> claims.claim("realm_access", Map.of("roles",
                        List.of("tool:sql", "cap:", "kb:moe", "kb:moe:write", "CAP:SQL", "cap:sql"))))
                .andExpect(jsonPath("$.data.permissions", contains("cap:sql")));
    }

    static Stream<Arguments> invalidTokens() {
        Instant past = Instant.now().minusSeconds(600);
        return Stream.of(
                Arguments.of("wrong issuer", token(c -> c.issuer("http://evil.example/realms/agent"))),
                Arguments.of("wrong audience", token(c -> c.audience(List.of("account")))),
                Arguments.of("no audience", token(c -> c.audience((String) null))),
                Arguments.of("expired", token(c -> c.issueTime(Date.from(past.minusSeconds(300)))
                        .expirationTime(Date.from(past)))),
                Arguments.of("signed by another key", sign(FOREIGN_KEY, c -> {})),
                Arguments.of("not a jwt", "not-a-token"));
    }

    @ParameterizedTest(name = "{0}")
    @MethodSource("invalidTokens")
    void invalidTokenIsRejectedWithEnvelope(String reason, String token) throws Exception {
        mvc.perform(get("/v1/me").header(HttpHeaders.AUTHORIZATION, bearer(token)))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.request_id").isNotEmpty())
                .andExpect(jsonPath("$.error.code").value("unauthorized"))
                .andExpect(jsonPath("$.error.message_ar").isNotEmpty())
                .andExpect(jsonPath("$.error.message_en").isNotEmpty())
                .andExpect(jsonPath("$.data").doesNotExist());
    }

    @Test
    void missingTokenIsRejectedWithEnvelope() throws Exception {
        mvc.perform(get("/v1/me"))
                .andExpect(status().isUnauthorized())
                .andExpect(header().string(HttpHeaders.WWW_AUTHENTICATE, "Bearer"))
                .andExpect(jsonPath("$.error.code").value("unauthorized"));
    }

    @Test
    void healthIsPublic() throws Exception {
        mvc.perform(get("/v1/health"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("ok"));
    }

    @Test
    void unknownPathForAuthenticatedUserIsNotFoundEnvelope() throws Exception {
        mvc.perform(get("/v1/nope").header(HttpHeaders.AUTHORIZATION, bearer(token())))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error.code").value("not_found"));
    }

    private ResultActions me(Consumer<JWTClaimsSet.Builder> customizer) throws Exception {
        return mvc.perform(get("/v1/me").header(HttpHeaders.AUTHORIZATION, bearer(token(customizer))));
    }
}
