package sa.ncai.agent.gateway.reports;

import java.io.IOException;
import java.io.Reader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.regex.Pattern;
import org.yaml.snakeyaml.LoaderOptions;
import org.yaml.snakeyaml.Yaml;
import org.yaml.snakeyaml.constructor.SafeConstructor;

/**
 * The report design kit the gateway serves (reports PR, I2): exactly the files config/artifacts/report.yaml's
 * {@code kit} lists (its stylesheet, scripts and fonts), read from the kit directory into memory once at
 * startup (about 600 KB), each at {@code kit.path} + its name. Nothing else under the directory is ever
 * served, and a name is looked up whole, so no request path reaches the file system. A missing, unlisted
 * type or oversized file fails the gateway, as a bad config/turn.yaml does.
 */
public final class ReportKit {

    /** One file as served: its bytes and its media type. */
    public record Asset(byte[] body, String contentType) {}

    /** What report.yaml's {@code kit.path} must look like; the agent accepts the same as kit_base. */
    private static final Pattern PATH = Pattern.compile("^/report-kit/v[0-9]+/$");
    /** A listed name: plain segments, no dot segment, no backslash. */
    private static final Pattern NAME = Pattern.compile("^[A-Za-z0-9_-][A-Za-z0-9._-]*(/[A-Za-z0-9_-][A-Za-z0-9._-]*)*$");
    /** One file, and the kit as a whole, read at most this large (CLAUDE.md rule 12d). */
    static final long MAX_FILE_BYTES = 4L * 1024 * 1024;
    static final long MAX_KIT_BYTES = 16L * 1024 * 1024;

    private static final Map<String, String> TYPES = Map.of(
            ".css", "text/css; charset=utf-8",
            ".js", "text/javascript; charset=utf-8",
            ".woff2", "font/woff2");

    private final String path;
    private final Map<String, Asset> assets;

    private ReportKit(String path, Map<String, Asset> assets) {
        this.path = path;
        this.assets = Map.copyOf(assets);
    }

    /** The kit's path on the gateway's origin, e.g. {@code /report-kit/v1/}. */
    public String path() {
        return path;
    }

    /** The file served at {@link #path()} + {@code name}, when the kit lists it. */
    public Optional<Asset> asset(String name) {
        return Optional.ofNullable(assets.get(name));
    }

    public static ReportKit load(Path config, Path directory) {
        try (Reader reader = Files.newBufferedReader(config, StandardCharsets.UTF_8)) {
            Map<?, ?> root = new Yaml(new SafeConstructor(new LoaderOptions())).load(reader);
            Map<?, ?> kit = (Map<?, ?>) root.get("kit");
            String path = String.valueOf(kit.get("path"));
            if (!PATH.matcher(path).matches()) {
                throw new IllegalArgumentException("kit.path must look like /report-kit/v1/, not " + path);
            }
            List<String> names = new ArrayList<>();
            names.add(String.valueOf(kit.get("stylesheet")));
            for (String list : List.of("scripts", "fonts")) {
                for (Object name : (List<?>) kit.get(list)) {
                    names.add(String.valueOf(name));
                }
            }
            Path base = directory.toRealPath();
            Map<String, Asset> assets = new LinkedHashMap<>();
            long total = 0;
            for (String name : names) {
                if (!NAME.matcher(name).matches()) {
                    throw new IllegalArgumentException("kit file name not allowed: " + name);
                }
                String type = TYPES.get(name.substring(Math.max(name.lastIndexOf('.'), 0)));
                if (type == null) {
                    throw new IllegalArgumentException("kit file of a type the gateway does not serve: " + name);
                }
                Path file = base.resolve(name).toRealPath();
                if (!file.startsWith(base) || !Files.isRegularFile(file)) {
                    throw new IllegalArgumentException("kit file outside the kit directory: " + name);
                }
                long size = Files.size(file);
                total += size;
                if (size > MAX_FILE_BYTES || total > MAX_KIT_BYTES) {
                    throw new IllegalArgumentException("kit file too large: " + name);
                }
                assets.put(name, new Asset(Files.readAllBytes(file), type));
            }
            return new ReportKit(path, assets);
        } catch (IOException | RuntimeException e) {
            throw new IllegalStateException(
                    "cannot load the report kit from " + config + " and " + directory + ": " + e.getMessage(), e);
        }
    }
}
