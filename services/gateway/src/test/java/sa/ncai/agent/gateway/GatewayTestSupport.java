package sa.ncai.agent.gateway;

import com.nimbusds.jose.JOSEObjectType;
import com.nimbusds.jose.JOSEException;
import com.nimbusds.jose.JWSAlgorithm;
import com.nimbusds.jose.JWSHeader;
import com.nimbusds.jose.crypto.RSASSASigner;
import com.nimbusds.jose.jwk.JWKSet;
import com.nimbusds.jose.jwk.RSAKey;
import com.nimbusds.jose.jwk.gen.RSAKeyGenerator;
import com.nimbusds.jwt.JWTClaimsSet;
import com.nimbusds.jwt.SignedJWT;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.time.Instant;
import java.util.Date;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;
import okhttp3.mockwebserver.Dispatcher;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.BeforeEach;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.actuate.observability.AutoConfigureObservability;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.context.bean.override.convention.TestBean;
import org.springframework.test.web.servlet.MockMvc;
import sa.ncai.agent.gateway.audit.AuditRepository;

/**
 * Shared context for gateway tests: a MockWebServer serves the JWKS, another plays the
 * agent, tokens are signed with a local RSA key, and the audit log is in memory.
 * No database, Keycloak or model is involved.
 */
@SpringBootTest
@AutoConfigureMockMvc
@AutoConfigureObservability(metrics = false)
@ActiveProfiles("test")
public abstract class GatewayTestSupport {

    protected static final String ISSUER = "http://localhost:8081/realms/agent";
    protected static final String AUDIENCE = "gateway";
    protected static final String INTERNAL_TOKEN = "test-internal-token";
    protected static final RSAKey SIGNING_KEY = rsaKey("test-key");
    protected static final RSAKey FOREIGN_KEY = rsaKey("test-key");

    protected static final MockWebServer JWKS = new MockWebServer();
    protected static final MockWebServer AGENT = new MockWebServer();

    static {
        JWKS.setDispatcher(new Dispatcher() {
            @Override
            public MockResponse dispatch(RecordedRequest request) {
                return new MockResponse()
                        .setHeader("Content-Type", "application/json")
                        .setBody(new JWKSet(SIGNING_KEY.toPublicJWK()).toString());
            }
        });
        try {
            JWKS.start();
            AGENT.start();
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    @DynamicPropertySource
    static void endpoints(DynamicPropertyRegistry registry) {
        registry.add("gateway.keycloak.jwks-uri", () -> JWKS.url("/realms/agent/protocol/openid-connect/certs").toString());
        registry.add("gateway.agent.base-url", () -> "http://" + AGENT.getHostName() + ":" + AGENT.getPort());
    }

    @TestBean(methodName = "inMemoryAudit")
    protected AuditRepository audit;

    @Autowired
    protected MockMvc mvc;

    static AuditRepository inMemoryAudit() {
        return new InMemoryAuditRepository();
    }

    @BeforeEach
    void resetSharedState() throws InterruptedException {
        auditLog().clear();
        while (AGENT.takeRequest(0, TimeUnit.MILLISECONDS) != null) {
            // drop requests left over from earlier tests
        }
    }

    protected InMemoryAuditRepository auditLog() {
        return (InMemoryAuditRepository) audit;
    }

    /** A Keycloak-shaped access token for "analyst"; {@code customizer} can override any claim. */
    protected static String token(Consumer<JWTClaimsSet.Builder> customizer) {
        return sign(SIGNING_KEY, customizer);
    }

    protected static String token() {
        return token(claims -> {});
    }

    protected static String sign(RSAKey key, Consumer<JWTClaimsSet.Builder> customizer) {
        Instant now = Instant.now();
        JWTClaimsSet.Builder claims = new JWTClaimsSet.Builder()
                .issuer(ISSUER)
                .audience(List.of(AUDIENCE, "account"))
                .subject("user-123")
                .issueTime(Date.from(now))
                .expirationTime(Date.from(now.plusSeconds(300)))
                .claim("preferred_username", "analyst")
                .claim("name", "سارة المحللة")
                .claim("tenant_id", "tenant-a")
                .claim("realm_access", Map.of("roles",
                        List.of("default-roles-agent", "user", "cap:general", "kb:moe:read", "offline_access")));
        customizer.accept(claims);
        try {
            SignedJWT jwt = new SignedJWT(
                    new JWSHeader.Builder(JWSAlgorithm.RS256).keyID(key.getKeyID()).type(JOSEObjectType.JWT).build(),
                    claims.build());
            jwt.sign(new RSASSASigner(key));
            return jwt.serialize();
        } catch (JOSEException e) {
            throw new IllegalStateException(e);
        }
    }

    protected static String bearer(String token) {
        return "Bearer " + token;
    }

    private static RSAKey rsaKey(String keyId) {
        try {
            return new RSAKeyGenerator(2048).keyID(keyId).generate();
        } catch (JOSEException e) {
            throw new IllegalStateException(e);
        }
    }
}
