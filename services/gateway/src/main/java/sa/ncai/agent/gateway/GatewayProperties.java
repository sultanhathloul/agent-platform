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
        @Valid @NotNull Models models) {

    /**
     * @param issuer   browser-facing issuer the tokens carry (validated, never fetched)
     * @param jwksUri  internal JWKS endpoint the gateway fetches signing keys from
     * @param audience value that must appear in the token's {@code aud}
     */
    public record Keycloak(@NotBlank String issuer, @NotBlank String jwksUri, @NotBlank String audience) {}

    public record Agent(
            @NotBlank String baseUrl,
            @NotBlank String internalToken,
            @NotNull Duration connectTimeout,
            @NotNull Duration responseTimeout) {}

    public record Cors(@NotEmpty List<String> allowedOrigins) {}

    /** @param config path of config/models.yaml, whose {@code caps.role:*} entries become {@code max_tier} */
    public record Models(@NotBlank String config) {}
}
