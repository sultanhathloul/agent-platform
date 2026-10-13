package sa.ncai.agent.gateway.attachments;

import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Optional;
import sa.ncai.agent.gateway.api.ErrorCode;

/**
 * Checks an upload's bytes against its kind without parsing or decompressing anything (owner
 * decision review round 2, finding 3): a PDF starts with {@code %PDF}; a DOCX or XLSX starts with
 * the ZIP local-file-header magic and names a {@code word/} or {@code xl/} entry somewhere in its
 * bytes (a raw byte scan, never an inflate); text holds no NUL byte in its head. The gateway
 * counts no PDF pages and opens no archive: the page limit (D2) and every content bound are
 * enforced in the ingest service's sandboxed child process (ADR-0176), which fails an over-limit
 * PDF with {@code too_many_pages} and its Arabic message. This mirrors the agent's own
 * {@code detect_kind} (app/rag/extract/base.py), so the two services agree on a file's kind.
 */
final class UploadInspector {

    private UploadInspector() {}

    private static final byte[] ZIP_MAGIC = {'P', 'K', 0x03, 0x04};

    /** The refusal for this file, if any. No parsing, no decompression, no page count. */
    static Optional<ErrorCode> inspect(String kind, Path file) throws IOException {
        byte[] head = readHead(file, 8192);
        return switch (kind) {
            case "pdf" -> startsWithPdf(head) ? Optional.empty() : Optional.of(ErrorCode.FILE_TYPE_UNSUPPORTED);
            case "docx" -> zipNames(file, "word/") ? Optional.empty() : Optional.of(ErrorCode.FILE_TYPE_UNSUPPORTED);
            case "xlsx" -> zipNames(file, "xl/") ? Optional.empty() : Optional.of(ErrorCode.FILE_TYPE_UNSUPPORTED);
            case "txt", "md" -> hasNul(head) ? Optional.of(ErrorCode.FILE_TYPE_UNSUPPORTED) : Optional.empty();
            default -> Optional.of(ErrorCode.FILE_TYPE_UNSUPPORTED);
        };
    }

    private static boolean startsWithPdf(byte[] head) {
        String start = new String(head, 0, Math.min(head.length, 1024), StandardCharsets.ISO_8859_1);
        return start.stripLeading().startsWith("%PDF");
    }

    /**
     * True when the file begins with the ZIP magic and the marker appears in its raw bytes. No
     * entry is inflated and no local header is parsed; a zip bomb is never expanded here (R4). The
     * marker is read in a bounded sliding window, so the whole file never enters memory at once.
     */
    private static boolean zipNames(Path file, String marker) throws IOException {
        byte[] magic = readHead(file, ZIP_MAGIC.length);
        if (magic.length < ZIP_MAGIC.length) {
            return false;
        }
        for (int i = 0; i < ZIP_MAGIC.length; i++) {
            if (magic[i] != ZIP_MAGIC[i]) {
                return false;
            }
        }
        byte[] needle = marker.getBytes(StandardCharsets.US_ASCII);
        byte[] window = new byte[64 * 1024];
        int carry = 0; // the last needle.length - 1 bytes of the previous window, to span the boundary
        try (InputStream in = Files.newInputStream(file)) {
            int read;
            while ((read = in.read(window, carry, window.length - carry)) != -1) {
                int available = carry + read;
                if (indexOf(window, available, needle) >= 0) {
                    return true;
                }
                carry = Math.min(needle.length - 1, available);
                System.arraycopy(window, available - carry, window, 0, carry);
            }
        }
        return false;
    }

    private static int indexOf(byte[] haystack, int length, byte[] needle) {
        outer:
        for (int i = 0; i + needle.length <= length; i++) {
            for (int j = 0; j < needle.length; j++) {
                if (haystack[i + j] != needle[j]) {
                    continue outer;
                }
            }
            return i;
        }
        return -1;
    }

    private static boolean hasNul(byte[] head) {
        for (byte b : head) {
            if (b == 0) {
                return true;
            }
        }
        return false;
    }

    private static byte[] readHead(Path file, int max) throws IOException {
        try (InputStream in = Files.newInputStream(file)) {
            return in.readNBytes(max);
        }
    }
}
