package sa.ncai.agent.gateway;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.NotNull;
import java.time.Duration;
import java.util.List;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.validation.annotation.Validated;

/** Gateway settings; env names follow relaxed binding (GATEWAY_KEYCLOAK_ISSUER, ...). */
@Validated
@ConfigurationProperties("gateway")
public record GatewayProperties(
        @Valid @NotNull Keycloak keycloak,
        @Valid @NotNull Agent agent,
        @Valid @NotNull Cors cors,
        @Valid @NotNull Models models,
        @Valid @NotNull Storage storage,
        @Valid @NotNull Docs docs,
        @Valid @NotNull Turn turn) {

    /**
     * @param issuer   browser-facing issuer the tokens carry (validated, never fetched)
     * @param jwksUri  internal JWKS endpoint the gateway fetches signing keys from
     * @param audience value that must appear in the token's {@code aud}
     * @param admin    the Admin REST API, reached with the gateway's service account (README §11)
     */
    public record Keycloak(
            @NotBlank String issuer, @NotBlank String jwksUri, @NotBlank String audience, Admin admin) {}

    /**
     * The Keycloak Admin REST API: internal base URL, realm, and the confidential client whose
     * service account reads realm roles (ADR-0107). Unset values make role reads fail with
     * {@code identity_unavailable}.
     */
    public record Admin(String baseUrl, String realm, String clientId, String clientSecret) {}

    /**
     * @param responseTimeout every agent call but a turn stream, from the request to the body's end
     *                        (Spring's JDK client applies its read timeout to the whole exchange;
     *                        a turn stream is bounded by {@link Turn} instead, ADR-0256)
     * @param keepAlive       how long an idle connection to the agent is kept; shorter than the
     *                        agent's own (uvicorn 75 s), set by {@link HttpClientKeepAlive} (I8)
     */
    public record Agent(
            @NotBlank String baseUrl,
            @NotBlank String internalToken,
            @NotNull Duration connectTimeout,
            @NotNull Duration responseTimeout,
            @NotNull Duration keepAlive) {}

    /** @param config path of config/turn.yaml: the relay's bound on a turn stream (I3, ADR-0256) */
    public record Turn(@NotBlank String config) {}

    public record Cors(@NotEmpty List<String> allowedOrigins) {}

    /** @param config path of config/models.yaml, whose {@code caps.role:*} entries become {@code max_tier} */
    public record Models(@NotBlank String config) {}

    /** MinIO, bucket {@code uploads} (ADR-0129); internal to the compose network. */
    public record Storage(@NotBlank String endpoint, String accessKey, String secretKey, @NotBlank String bucket) {}

    /**
     * @param config          path of config/docs.yaml, whose {@code limits} the gateway enforces (ADR-0126)
     * @param uploadsPerMinute per-user upload rate, from the environment per deployment (review round 2,
     *                         finding 4; GATEWAY_DOCS_UPLOADS_PER_MINUTE). When unset or empty,
     *                         config/docs.yaml's {@code limits.uploads_per_minute} applies.
     */
    public record Docs(@NotBlank String config, Integer uploadsPerMinute) {}
}
