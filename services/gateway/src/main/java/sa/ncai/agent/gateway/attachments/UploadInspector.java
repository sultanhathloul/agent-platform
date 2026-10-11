package sa.ncai.agent.gateway.attachments;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Optional;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;
import org.apache.pdfbox.Loader;
import org.apache.pdfbox.pdmodel.PDDocument;
import sa.ncai.agent.gateway.api.ErrorCode;

/**
 * Checks an upload's bytes against its kind before anything is stored (ADR-0126): a PDF starts
 * with {@code %PDF} and has at most {@code max_pdf_pages} pages; a DOCX or XLSX is a ZIP holding
 * {@code word/} or {@code xl/}; text holds no NUL byte. A PDF PDFBox cannot open (encrypted,
 * damaged) passes here, and the agent's ingestion reports why it cannot be read.
 */
final class UploadInspector {

    private UploadInspector() {}

    /** The refusal for this file, if any; also returns the PDF page count when it was counted. */
    static Inspection inspect(String kind, Path file, int maxPdfPages) throws IOException {
        byte[] head = readHead(file, 8192);
        return switch (kind) {
            case "pdf" -> inspectPdf(head, file, maxPdfPages);
            case "docx" -> zipHolds(file, "word/") ? Inspection.ok(null) : Inspection.refused(ErrorCode.FILE_TYPE_UNSUPPORTED);
            case "xlsx" -> zipHolds(file, "xl/") ? Inspection.ok(null) : Inspection.refused(ErrorCode.FILE_TYPE_UNSUPPORTED);
            case "txt", "md" -> hasNul(head) ? Inspection.refused(ErrorCode.FILE_TYPE_UNSUPPORTED) : Inspection.ok(null);
            default -> Inspection.refused(ErrorCode.FILE_TYPE_UNSUPPORTED);
        };
    }

    private static Inspection inspectPdf(byte[] head, Path file, int maxPages) {
        String start = new String(head, 0, Math.min(head.length, 1024), java.nio.charset.StandardCharsets.ISO_8859_1);
        if (!start.stripLeading().startsWith("%PDF")) {
            return Inspection.refused(ErrorCode.FILE_TYPE_UNSUPPORTED);
        }
        try (PDDocument document = Loader.loadPDF(file.toFile())) {
            int pages = document.getNumberOfPages();
            return pages > maxPages ? new Inspection(Optional.of(ErrorCode.TOO_MANY_PAGES), pages) : Inspection.ok(pages);
        } catch (IOException | RuntimeException e) {
            return Inspection.ok(null);
        }
    }

    private static boolean zipHolds(Path file, String prefix) throws IOException {
        try (ZipInputStream zip = new ZipInputStream(Files.newInputStream(file))) {
            ZipEntry entry;
            while ((entry = zip.getNextEntry()) != null) {
                if (entry.getName().startsWith(prefix)) {
                    return true;
                }
            }
            return false;
        } catch (IOException | IllegalArgumentException e) {
            return false;
        }
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

    record Inspection(Optional<ErrorCode> refusal, Integer pages) {
        static Inspection ok(Integer pages) {
            return new Inspection(Optional.empty(), pages);
        }

        static Inspection refused(ErrorCode code) {
            return new Inspection(Optional.of(code), null);
        }
    }
}
