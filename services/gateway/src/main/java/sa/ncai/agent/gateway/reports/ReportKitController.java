package sa.ncai.agent.gateway.reports;

import io.swagger.v3.oas.annotations.Hidden;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.HttpHeaders;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * The report kit's files (reports PR, I2), public and without a token: a report page in its sandboxed
 * frame has an opaque origin and loads them by its own {@code <link>} and {@code <script src>}. Only the
 * files report.yaml lists are served, by exact name; anything else is 404 without a body (no listing).
 * {@code Access-Control-Allow-Origin: *}: a font load is a CORS request, from origin {@code null} here;
 * the files are the same for everyone and carry nothing of a user's. Outside the main security chain, so
 * neither the gateway's CORS rules nor its frame and cache headers apply (SecurityConfig).
 */
@Hidden
@RestController
public class ReportKitController {

    static final String PATHS = "/report-kit/**";

    private final ReportKit kit;

    public ReportKitController(ReportKit kit) {
        this.kit = kit;
    }

    @GetMapping(PATHS)
    public ResponseEntity<byte[]> asset(HttpServletRequest request) {
        String uri = request.getRequestURI().substring(request.getContextPath().length());
        if (!uri.startsWith(kit.path())) {
            return ResponseEntity.notFound().build();
        }
        return kit.asset(uri.substring(kit.path().length()))
                .map(asset -> ResponseEntity.ok()
                        .header(HttpHeaders.CONTENT_TYPE, asset.contentType())
                        .header(HttpHeaders.ACCESS_CONTROL_ALLOW_ORIGIN, "*")
                        .header("Cross-Origin-Resource-Policy", "cross-origin")
                        .header("X-Content-Type-Options", "nosniff")
                        // The version is in the path: a kit change is a new path, never a stale file.
                        .header(HttpHeaders.CACHE_CONTROL, "public, max-age=86400")
                        .body(asset.body()))
                .orElseGet(() -> ResponseEntity.notFound().build());
    }
}
