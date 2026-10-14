package sa.ncai.agent.gateway.auth;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.security.web.firewall.FirewalledRequest;
import org.springframework.security.web.firewall.HttpFirewall;
import org.springframework.security.web.firewall.RequestRejectedException;
import org.springframework.security.web.firewall.StrictHttpFirewall;

/**
 * Spring Security's strict firewall, and before it one rule of the gateway's (the verification of
 * review round 2): a request path must be written plainly. A path holding a percent-encoded
 * character (so any double-encoded sequence too), a matrix parameter ({@code ;}), a backslash, a
 * dot segment or a doubled slash is rejected with 400 before authentication, because no platform
 * path needs any of them and each is a second spelling of a path that a filter matching the raw
 * URI could miss. The rule applies to every path, not only {@code /v1/**}: deciding whether a raw
 * path is under {@code /v1} could itself be fooled by an encoded spelling. The query string is not
 * part of the path and is not checked.
 */
public final class PathFirewall implements HttpFirewall {

    private final StrictHttpFirewall strict = new StrictHttpFirewall();

    @Override
    public FirewalledRequest getFirewalledRequest(HttpServletRequest request) {
        if (!isPlain(request.getRequestURI())) {
            throw new RequestRejectedException("the request path is not written plainly");
        }
        return strict.getFirewalledRequest(request);
    }

    @Override
    public HttpServletResponse getFirewalledResponse(HttpServletResponse response) {
        return strict.getFirewalledResponse(response);
    }

    /** True when the raw path has no encoding, matrix parameter, backslash, dot segment or doubled slash. */
    static boolean isPlain(String path) {
        if (path == null || path.isEmpty()) {
            return true;
        }
        if (path.indexOf('%') >= 0 || path.indexOf(';') >= 0 || path.indexOf('\\') >= 0 || path.contains("//")) {
            return false;
        }
        for (String segment : path.split("/", -1)) {
            if (segment.equals(".") || segment.equals("..")) {
                return false;
            }
        }
        return true;
    }
}
