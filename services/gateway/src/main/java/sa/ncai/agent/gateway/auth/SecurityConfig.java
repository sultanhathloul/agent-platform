package sa.ncai.agent.gateway.auth;

import java.util.List;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.security.authentication.AbstractAuthenticationToken;
import org.springframework.security.config.Customizer;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configurers.AbstractHttpConfigurer;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.oauth2.core.DelegatingOAuth2TokenValidator;
import org.springframework.security.oauth2.core.OAuth2Error;
import org.springframework.security.oauth2.core.OAuth2ErrorCodes;
import org.springframework.security.oauth2.core.OAuth2TokenValidator;
import org.springframework.security.oauth2.core.OAuth2TokenValidatorResult;
import org.springframework.security.oauth2.jose.jws.SignatureAlgorithm;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.security.oauth2.jwt.JwtIssuerValidator;
import org.springframework.security.oauth2.jwt.JwtTimestampValidator;
import org.springframework.security.oauth2.jwt.NimbusJwtDecoder;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken;
import org.springframework.security.web.AuthenticationEntryPoint;
import org.springframework.security.oauth2.server.resource.web.authentication.BearerTokenAuthenticationFilter;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.access.AccessDeniedHandler;
import org.springframework.web.cors.CorsConfiguration;
import org.springframework.web.cors.CorsConfigurationSource;
import org.springframework.web.cors.UrlBasedCorsConfigurationSource;
import sa.ncai.agent.gateway.GatewayProperties;
import sa.ncai.agent.gateway.api.ErrorCode;
import sa.ncai.agent.gateway.api.ErrorResponses;
import sa.ncai.agent.gateway.api.RequestIdFilter;
import sa.ncai.agent.gateway.attachments.AttachmentGate;

/** OIDC resource server: Keycloak JWTs checked for signature (JWKS), expiry, issuer and audience. */
@Configuration(proxyBeanMethods = false)
public class SecurityConfig {

    private static final String[] PUBLIC_PATHS = {
        "/v1/health", "/actuator/health", "/actuator/health/**", "/v3/api-docs", "/v3/api-docs/**", "/error"
    };

    @Bean
    SecurityFilterChain securityFilterChain(HttpSecurity http, ErrorResponses errors, AttachmentGate attachmentGate)
            throws Exception {
        AuthenticationEntryPoint unauthorized = (request, response, e) -> {
            response.setHeader(HttpHeaders.WWW_AUTHENTICATE, "Bearer");
            errors.write(request, response, ErrorCode.UNAUTHORIZED);
        };
        AccessDeniedHandler forbidden = (request, response, e) -> errors.write(request, response, ErrorCode.FORBIDDEN);

        http.csrf(AbstractHttpConfigurer::disable)
                .cors(Customizer.withDefaults())
                .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .requestCache(AbstractHttpConfigurer::disable)
                .authorizeHttpRequests(auth -> auth
                        .requestMatchers(HttpMethod.OPTIONS, "/**").permitAll()
                        .requestMatchers(PUBLIC_PATHS).permitAll()
                        .anyRequest().authenticated())
                .oauth2ResourceServer(oauth -> oauth
                        .jwt(jwt -> jwt.jwtAuthenticationConverter(SecurityConfig::authentication))
                        .authenticationEntryPoint(unauthorized)
                        .accessDeniedHandler(forbidden))
                .exceptionHandling(handling -> handling
                        .authenticationEntryPoint(unauthorized)
                        .accessDeniedHandler(forbidden))
                // cap:docs, the declared size and the upload rate, before any body is read (R3).
                .addFilterAfter(attachmentGate, BearerTokenAuthenticationFilter.class);
        return http.build();
    }

    /**
     * Keys come from the internal JWKS URI; the issuer is the browser-facing URL and is only
     * compared, never fetched (it is not reachable from inside the compose network).
     */
    @Bean
    JwtDecoder jwtDecoder(GatewayProperties properties) {
        GatewayProperties.Keycloak keycloak = properties.keycloak();
        NimbusJwtDecoder decoder = NimbusJwtDecoder.withJwkSetUri(keycloak.jwksUri())
                .jwsAlgorithm(SignatureAlgorithm.RS256)
                .build();
        decoder.setJwtValidator(new DelegatingOAuth2TokenValidator<>(
                new JwtTimestampValidator(),
                new JwtIssuerValidator(keycloak.issuer()),
                audienceValidator(keycloak.audience())));
        return decoder;
    }

    static OAuth2TokenValidator<Jwt> audienceValidator(String audience) {
        OAuth2Error missing = new OAuth2Error(OAuth2ErrorCodes.INVALID_TOKEN, "The required audience is missing", null);
        return jwt -> jwt.getAudience() != null && jwt.getAudience().contains(audience)
                ? OAuth2TokenValidatorResult.success()
                : OAuth2TokenValidatorResult.failure(missing);
    }

    /** Realm roles become {@code ROLE_<role>} authorities. */
    private static AbstractAuthenticationToken authentication(Jwt jwt) {
        List<SimpleGrantedAuthority> authorities = KeycloakRoles.realmRoles(jwt).stream()
                .map(role -> new SimpleGrantedAuthority("ROLE_" + role))
                .toList();
        return new JwtAuthenticationToken(jwt, authorities, jwt.getSubject());
    }

    @Bean
    CorsConfigurationSource corsConfigurationSource(GatewayProperties properties) {
        CorsConfiguration cors = new CorsConfiguration();
        cors.setAllowedOrigins(properties.cors().allowedOrigins());
        cors.setAllowedMethods(List.of("GET", "POST", "PUT", "DELETE", "OPTIONS"));
        cors.setAllowedHeaders(List.of(
                HttpHeaders.AUTHORIZATION, HttpHeaders.CONTENT_TYPE, HttpHeaders.ACCEPT,
                HttpHeaders.ACCEPT_LANGUAGE, RequestIdFilter.HEADER));
        cors.setExposedHeaders(List.of(RequestIdFilter.HEADER));
        cors.setMaxAge(3600L);
        UrlBasedCorsConfigurationSource source = new UrlBasedCorsConfigurationSource();
        source.registerCorsConfiguration("/**", cors);
        return source;
    }
}
