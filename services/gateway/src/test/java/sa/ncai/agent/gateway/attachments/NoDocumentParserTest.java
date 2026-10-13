package sa.ncai.agent.gateway.attachments;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;

/**
 * The gateway parses no document (owner decision review round 2, finding 3): no PDF, archive or
 * office-document library is on its classpath, so no uploaded file can be decompressed or parsed
 * in the gateway's own process. Type is checked by magic bytes only ({@link UploadInspector}); the
 * ingest service, in a sandboxed child process, is the only place a document is read (ADR-0176).
 */
class NoDocumentParserTest {

    @Test
    void noDocumentParserIsOnTheClasspath() {
        for (String parser : new String[] {
            "org.apache.pdfbox.Loader", // PDFBox 3
            "org.apache.pdfbox.pdmodel.PDDocument", // PDFBox 2 and 3
            "org.apache.poi.openxml4j.opc.OPCPackage", // Apache POI (DOCX/XLSX)
            "org.apache.tika.Tika", // Tika
            "com.itextpdf.kernel.pdf.PdfDocument", // iText
        }) {
            assertThat(onClasspath(parser)).as("%s must not be on the gateway classpath", parser).isFalse();
        }
    }

    private static boolean onClasspath(String className) {
        try {
            Class.forName(className, false, NoDocumentParserTest.class.getClassLoader());
            return true;
        } catch (ClassNotFoundException | LinkageError e) {
            return false;
        }
    }
}
