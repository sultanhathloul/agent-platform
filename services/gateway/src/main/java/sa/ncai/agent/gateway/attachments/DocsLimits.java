package sa.ncai.agent.gateway.attachments;

import java.io.IOException;
import java.io.Reader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import org.yaml.snakeyaml.LoaderOptions;
import org.yaml.snakeyaml.Yaml;
import org.yaml.snakeyaml.constructor.SafeConstructor;

/**
 * Upload limits from {@code config/docs.yaml} (ADR-0126), the file the agent reads too, so the
 * two services enforce the same values: bytes per file, files per conversation, pages per PDF,
 * and the allowed kinds by extension.
 */
public record DocsLimits(long maxFileBytes, int maxFilesPerConversation, int maxPdfPages, Map<String, String> kinds) {

    public static DocsLimits load(Path path) {
        try (Reader reader = Files.newBufferedReader(path, StandardCharsets.UTF_8)) {
            Map<?, ?> config = new Yaml(new SafeConstructor(new LoaderOptions())).load(reader);
            Map<?, ?> limits = (Map<?, ?>) config.get("limits");
            Map<String, String> kinds = new LinkedHashMap<>();
            for (Map.Entry<?, ?> type : ((Map<?, ?>) limits.get("types")).entrySet()) {
                for (Object extension : (List<?>) ((Map<?, ?>) type.getValue()).get("extensions")) {
                    kinds.put(extension.toString().toLowerCase(Locale.ROOT), type.getKey().toString());
                }
            }
            return new DocsLimits(
                    ((Number) limits.get("max_file_bytes")).longValue(),
                    ((Number) limits.get("max_files_per_conversation")).intValue(),
                    ((Number) limits.get("max_pdf_pages")).intValue(),
                    Map.copyOf(kinds));
        } catch (IOException | RuntimeException e) {
            throw new IllegalStateException("cannot read upload limits from " + path + ": " + e.getMessage(), e);
        }
    }

    /** The kind (pdf, docx, xlsx, txt, md) a file name's extension allows, if any. */
    public Optional<String> kindOf(String fileName) {
        if (fileName == null) {
            return Optional.empty();
        }
        int dot = fileName.lastIndexOf('.');
        if (dot < 0 || dot == fileName.length() - 1) {
            return Optional.empty();
        }
        return Optional.ofNullable(kinds.get(fileName.substring(dot + 1).toLowerCase(Locale.ROOT)));
    }
}
