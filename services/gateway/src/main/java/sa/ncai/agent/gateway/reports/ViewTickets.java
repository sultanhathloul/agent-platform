package sa.ncai.agent.gateway.reports;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import java.io.IOException;
import java.security.GeneralSecurityException;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.Base64;
import java.util.Optional;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;
import sa.ncai.agent.gateway.auth.GatewayPrincipal;

/**
 * The report page's view tickets (reports PR, I4): the panel's sandboxed frame loads the page by a
 * navigation, which carries no bearer token (the web keeps tokens in memory), so the gateway gives the
 * owner a link that names the caller, the report, the version and an expiry 60 s ahead, signed with
 * HMAC-SHA256: {@code base64url(payload) "." base64url(mac)}. A ticket is checked in constant time and
 * grants that one version's page to that one caller until it expires; nothing is stored.
 *
 * <p>The key is 32 random bytes made at startup and held in memory only, so a restart voids every
 * ticket (the panel asks for a new one) and a ticket works only on the gateway that issued it: v1 runs
 * one gateway replica. More replicas need a shared key (a mounted secret) or sticky routing.
 */
@Component
public class ViewTickets {

    /** How long a link stays valid: enough to load the frame, too short to be worth passing on. */
    static final Duration TTL = Duration.ofSeconds(60);
    /** A ticket longer than this is refused before any decoding (CLAUDE.md rule 12d). */
    static final int MAX_CHARS = 1024;

    private static final String ALGORITHM = "HmacSHA256";
    private static final Base64.Encoder ENCODER = Base64.getUrlEncoder().withoutPadding();
    private static final Base64.Decoder DECODER = Base64.getUrlDecoder();

    /** What a valid ticket grants. */
    public record Ticket(String userId, String tenantId, String reportId, int version, Instant expiresAt) {}

    /** A ticket and when it expires. */
    public record Issued(String ticket, Instant expiresAt) {}

    private final ObjectMapper mapper = new ObjectMapper();
    private final SecretKeySpec key;
    private final Clock clock;

    @Autowired
    public ViewTickets() {
        this(Clock.systemUTC());
    }

    ViewTickets(Clock clock) {
        byte[] secret = new byte[32];
        new SecureRandom().nextBytes(secret);
        this.key = new SecretKeySpec(secret, ALGORITHM);
        this.clock = clock;
    }

    public Issued issue(GatewayPrincipal caller, String reportId, int version) {
        Instant expiresAt = clock.instant().plus(TTL).truncatedTo(ChronoUnit.SECONDS);
        ArrayNode payload = mapper.createArrayNode()
                .add(caller.userId())
                .add(caller.tenantId())
                .add(reportId)
                .add(version)
                .add(expiresAt.getEpochSecond());
        byte[] body;
        try {
            body = mapper.writeValueAsBytes(payload);
        } catch (IOException e) {
            throw new IllegalStateException(e);
        }
        return new Issued(ENCODER.encodeToString(body) + "." + ENCODER.encodeToString(mac(body)), expiresAt);
    }

    /** The ticket's grant when its signature holds and it has not expired; empty otherwise, for any reason. */
    public Optional<Ticket> verify(String ticket) {
        if (ticket == null || ticket.length() > MAX_CHARS) {
            return Optional.empty();
        }
        int dot = ticket.indexOf('.');
        if (dot <= 0 || dot != ticket.lastIndexOf('.')) {
            return Optional.empty();
        }
        byte[] body;
        byte[] given;
        try {
            body = DECODER.decode(ticket.substring(0, dot));
            given = DECODER.decode(ticket.substring(dot + 1));
        } catch (IllegalArgumentException e) {
            return Optional.empty();
        }
        if (!MessageDigest.isEqual(mac(body), given)) {
            return Optional.empty();
        }
        try {
            JsonNode payload = mapper.readTree(body);
            if (!payload.isArray() || payload.size() != 5 || !payload.get(3).canConvertToInt()
                    || !payload.get(4).canConvertToLong()) {
                return Optional.empty();
            }
            Instant expiresAt = Instant.ofEpochSecond(payload.get(4).asLong());
            if (!clock.instant().isBefore(expiresAt)) {
                return Optional.empty();
            }
            return Optional.of(new Ticket(payload.get(0).asText(), payload.get(1).asText(), payload.get(2).asText(),
                    payload.get(3).asInt(), expiresAt));
        } catch (IOException e) {
            return Optional.empty();
        }
    }

    private byte[] mac(byte[] body) {
        try {
            Mac mac = Mac.getInstance(ALGORITHM);
            mac.init(key);
            return mac.doFinal(body);
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException(e);
        }
    }
}
