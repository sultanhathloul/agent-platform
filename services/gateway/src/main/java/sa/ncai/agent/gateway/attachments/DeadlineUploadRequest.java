package sa.ncai.agent.gateway.attachments;

import jakarta.servlet.MultipartConfigElement;
import jakarta.servlet.ReadListener;
import jakarta.servlet.ServletException;
import jakarta.servlet.ServletInputStream;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletRequestWrapper;
import jakarta.servlet.http.Part;
import java.io.File;
import java.io.IOException;
import java.time.Clock;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.Enumeration;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.apache.catalina.core.ApplicationPart;
import org.apache.tomcat.util.http.fileupload.FileItem;
import org.apache.tomcat.util.http.fileupload.FileUpload;
import org.apache.tomcat.util.http.fileupload.FileUploadException;
import org.apache.tomcat.util.http.fileupload.disk.DiskFileItemFactory;
import org.apache.tomcat.util.http.fileupload.servlet.ServletRequestContext;

/**
 * An admitted upload whose body must arrive by a deadline (the verification of review round 2).
 * Tomcat's own multipart parsing reads its internal stream, which no wrapper can bound, and its
 * read timeout is per read, so a trickled body could hold a slot for as long as it kept trickling.
 * This wrapper therefore parses the parts itself, with Tomcat's parser and the same limits
 * (multipart config, 512-byte part headers, 10 parts — Tomcat's defaults), from a stream that
 * refuses every read past the deadline. A read already blocked when the deadline passes ends at
 * the latest after {@code server.tomcat.connection-upload-timeout}; the body is then refused with
 * {@link UploadDeadlineExceededException} (408 {@code upload_timeout}).
 *
 * <p>Text parts (no file name) are the request's parameters, read as UTF-8, each at most
 * {@link #MAX_FIELD_BYTES}: Tomcat never parsed the body, so without this a form field beside the
 * file (a knowledge base document's source metadata, phase 08) would be invisible.
 */
final class DeadlineUploadRequest extends HttpServletRequestWrapper {

    private static final int MAX_PART_HEADER_BYTES = 512;
    private static final int MAX_PARTS = 10;
    /** A text field beside the file; a longer one is not a parameter. */
    static final int MAX_FIELD_BYTES = 8192;

    private final Instant deadline;
    private final Clock clock;
    private final MultipartConfigElement multipart;
    private List<Part> parts;
    private Map<String, String[]> fields;

    DeadlineUploadRequest(HttpServletRequest request, Instant deadline, Clock clock, MultipartConfigElement multipart) {
        super(request);
        this.deadline = deadline;
        this.clock = clock;
        this.multipart = multipart;
    }

    @Override
    public ServletInputStream getInputStream() throws IOException {
        return new DeadlineInputStream(super.getInputStream());
    }

    @Override
    public Collection<Part> getParts() throws IOException, ServletException {
        if (parts == null) {
            parts = parse();
        }
        return parts;
    }

    @Override
    public Part getPart(String name) throws IOException, ServletException {
        for (Part part : getParts()) {
            if (part.getName().equals(name)) {
                return part;
            }
        }
        return null;
    }

    @Override
    public String getParameter(String name) {
        String[] values = fieldMap().get(name);
        return values == null || values.length == 0 ? super.getParameter(name) : values[0];
    }

    @Override
    public String[] getParameterValues(String name) {
        String[] values = fieldMap().get(name);
        return values == null ? super.getParameterValues(name) : values.clone();
    }

    @Override
    public Map<String, String[]> getParameterMap() {
        Map<String, String[]> merged = new LinkedHashMap<>(super.getParameterMap());
        merged.putAll(fieldMap());
        return Collections.unmodifiableMap(merged);
    }

    @Override
    public Enumeration<String> getParameterNames() {
        return Collections.enumeration(getParameterMap().keySet());
    }

    /** The text parts, parsed once with the parts; none when the body cannot be read. */
    private Map<String, String[]> fieldMap() {
        if (fields != null) {
            return fields;
        }
        Map<String, List<String>> read = new LinkedHashMap<>();
        try {
            for (Part part : getParts()) {
                if (part.getSubmittedFileName() != null || part.getSize() > MAX_FIELD_BYTES) {
                    continue;
                }
                try (var in = part.getInputStream()) {
                    read.computeIfAbsent(part.getName(), k -> new ArrayList<>())
                            .add(new String(in.readNBytes(MAX_FIELD_BYTES), java.nio.charset.StandardCharsets.UTF_8));
                }
            }
        } catch (IOException | ServletException | IllegalStateException e) {
            // The controller's own read of the parts reports why (408, 413).
            fields = Map.of();
            return fields;
        }
        Map<String, String[]> out = new LinkedHashMap<>();
        read.forEach((key, values) -> out.put(key, values.toArray(String[]::new)));
        fields = out;
        return fields;
    }

    /** Removes the parts' temporary files, if any were parsed. */
    void deleteParts() {
        if (parts == null) {
            return;
        }
        for (Part part : parts) {
            try {
                part.delete();
            } catch (IOException ignored) {
                // a temporary file already gone
            }
        }
    }

    private List<Part> parse() throws IOException {
        File location = location();
        DiskFileItemFactory factory = new DiskFileItemFactory();
        factory.setRepository(location.getCanonicalFile());
        factory.setSizeThreshold(multipart.getFileSizeThreshold());
        FileUpload upload = new FileUpload();
        upload.setFileItemFactory(factory);
        upload.setFileSizeMax(multipart.getMaxFileSize());
        upload.setSizeMax(multipart.getMaxRequestSize());
        upload.setPartHeaderSizeMax(MAX_PART_HEADER_BYTES);
        upload.setFileCountMax(MAX_PARTS);
        try {
            List<Part> parsed = new ArrayList<>();
            for (FileItem item : upload.parseRequest(new ServletRequestContext(this))) {
                parsed.add(new ApplicationPart(item, location));
            }
            return parsed;
        } catch (FileUploadException e) {
            if (UploadDeadlineExceededException.in(e)) {
                throw new UploadDeadlineExceededException();
            }
            // As Tomcat does: Spring turns a size refusal into MaxUploadSizeExceededException (413).
            throw new IllegalStateException(e);
        }
    }

    private File location() {
        File temp = (File) getServletContext().getAttribute("jakarta.servlet.context.tempdir");
        String configured = multipart.getLocation();
        if (configured == null || configured.isEmpty()) {
            return temp;
        }
        File file = new File(configured);
        return file.isAbsolute() ? file : new File(temp, configured);
    }

    private void check() throws UploadDeadlineExceededException {
        if (clock.instant().isAfter(deadline)) {
            throw new UploadDeadlineExceededException();
        }
    }

    /** Refuses every read, and every read's result, past the deadline. */
    private final class DeadlineInputStream extends ServletInputStream {

        private final ServletInputStream in;

        DeadlineInputStream(ServletInputStream in) {
            this.in = in;
        }

        @Override
        public int read() throws IOException {
            check();
            int b = in.read();
            check();
            return b;
        }

        @Override
        public int read(byte[] buffer, int offset, int length) throws IOException {
            check();
            int read = in.read(buffer, offset, length);
            check();
            return read;
        }

        @Override
        public boolean isFinished() {
            return in.isFinished();
        }

        @Override
        public boolean isReady() {
            return in.isReady();
        }

        @Override
        public void setReadListener(ReadListener listener) {
            in.setReadListener(listener);
        }
    }
}
