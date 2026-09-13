package sa.ncai.agent.gateway.authz;

import java.util.List;
import org.springframework.stereotype.Component;
import sa.ncai.agent.gateway.auth.GatewayPrincipal;

/** Selected KBs must be a subset of the KBs the user can read (README §6.1.3, §9.4). */
@Component
public class KbAccessPolicy {

    public static String readPermission(String slug) {
        return "kb:" + slug + ":read";
    }

    /** The selected KB slugs the principal may not read; empty means access is granted. */
    public List<String> denied(GatewayPrincipal principal, List<String> selectedKbs) {
        return selectedKbs.stream()
                .distinct()
                .filter(slug -> !principal.permissions().contains(readPermission(slug)))
                .toList();
    }
}
