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
 * the allowed kinds by extension, and uploads per user per minute (owner decision R3). The
 * multipart caps derive from the same file: there is no other upload limit in the gateway.
 */
public record DocsLimits(
        long maxFileBytes,
        int maxFilesPerConversation,
        int maxPdfPages,
        Map<String, String> kinds,
        int uploadsPerMinute,
        int maxConcurrentUploads) {

    /** This file's limits with the per-minute rate replaced (the env override, review round 2). */
    public DocsLimits withUploadsPerMinute(int perMinute) {
        return new DocsLimits(
                maxFileBytes, maxFilesPerConversation, maxPdfPages, kinds, perMinute, maxConcurrentUploads);
    }

    /** Room for the multipart envelope around one file: field headers and boundaries. */
    static final long ENVELOPE_BYTES = 1024 * 1024;

    /** The largest request body an upload may have; anything larger is refused before it is read. */
    public long maxRequestBytes() {
        return maxFileBytes + 2 * ENVELOPE_BYTES;
    }

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
            Object concurrent = limits.get("max_concurrent_uploads");
            return new DocsLimits(
                    ((Number) limits.get("max_file_bytes")).longValue(),
                    ((Number) limits.get("max_files_per_conversation")).intValue(),
                    ((Number) limits.get("max_pdf_pages")).intValue(),
                    Map.copyOf(kinds),
                    ((Number) limits.get("uploads_per_minute")).intValue(),
                    concurrent == null ? 2 : ((Number) concurrent).intValue());
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
