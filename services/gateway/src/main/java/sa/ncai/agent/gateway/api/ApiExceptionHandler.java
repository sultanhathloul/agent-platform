package sa.ncai.agent.gateway.api;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.web.HttpMediaTypeNotAcceptableException;
import org.springframework.web.HttpMediaTypeNotSupportedException;
import org.springframework.web.HttpRequestMethodNotSupportedException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.MissingServletRequestParameterException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.HandlerMethodValidationException;
import org.springframework.web.multipart.MaxUploadSizeExceededException;
import org.springframework.web.multipart.MultipartException;
import org.springframework.web.multipart.support.MissingServletRequestPartException;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;
import org.springframework.web.servlet.resource.NoResourceFoundException;
import sa.ncai.agent.gateway.attachments.UploadDeadlineExceededException;

/** Maps every MVC failure to the error envelope; nothing leaves the gateway as HTML or ProblemDetail. */
@RestControllerAdvice
public class ApiExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(ApiExceptionHandler.class);

    private final ErrorResponses errors;

    public ApiExceptionHandler(ErrorResponses errors) {
        this.errors = errors;
    }

    @ExceptionHandler({
        MethodArgumentNotValidException.class,
        HandlerMethodValidationException.class,
        HttpMessageNotReadableException.class,
        MethodArgumentTypeMismatchException.class,
        MissingServletRequestParameterException.class
    })
    void invalidRequest(HttpServletRequest request, HttpServletResponse response) throws IOException {
        errors.write(request, response, ErrorCode.INVALID_REQUEST);
    }

    /** Over the multipart limit, before the controller runs: the upload limit's own message (ADR-0126). */
    @ExceptionHandler(MaxUploadSizeExceededException.class)
    void tooLarge(HttpServletRequest request, HttpServletResponse response) throws IOException {
        errors.write(request, response, ErrorCode.FILE_TOO_LARGE);
    }

    /**
     * A malformed multipart body is 400; one that did not arrive within the upload deadline is 408
     * {@code upload_timeout}, and the connection closes rather than read the rest of the body.
     */
    @ExceptionHandler({MissingServletRequestPartException.class, MultipartException.class})
    void badMultipart(Exception error, HttpServletRequest request, HttpServletResponse response) throws IOException {
        if (UploadDeadlineExceededException.in(error)) {
            response.setHeader(org.springframework.http.HttpHeaders.CONNECTION, "close");
            errors.write(request, response, ErrorCode.UPLOAD_TIMEOUT);
            return;
        }
        errors.write(request, response, ErrorCode.INVALID_REQUEST);
    }

    @ExceptionHandler(HttpMediaTypeNotSupportedException.class)
    void unsupportedMediaType(HttpServletRequest request, HttpServletResponse response) throws IOException {
        errors.write(request, response, ErrorCode.INVALID_REQUEST, 415);
    }

    @ExceptionHandler(HttpMediaTypeNotAcceptableException.class)
    void notAcceptable(HttpServletRequest request, HttpServletResponse response) throws IOException {
        errors.write(request, response, ErrorCode.INVALID_REQUEST, 406);
    }

    @ExceptionHandler(HttpRequestMethodNotSupportedException.class)
    void methodNotAllowed(HttpServletRequest request, HttpServletResponse response) throws IOException {
        errors.write(request, response, ErrorCode.INVALID_REQUEST, 405);
    }

    @ExceptionHandler(NoResourceFoundException.class)
    void notFound(HttpServletRequest request, HttpServletResponse response) throws IOException {
        errors.write(request, response, ErrorCode.NOT_FOUND);
    }

    @ExceptionHandler(AccessDeniedException.class)
    void forbidden(HttpServletRequest request, HttpServletResponse response) throws IOException {
        errors.write(request, response, ErrorCode.FORBIDDEN);
    }

    @ExceptionHandler(Exception.class)
    void unexpected(Exception e, HttpServletRequest request, HttpServletResponse response) throws IOException {
        log.error("unhandled error", e);
        errors.write(request, response, ErrorCode.INTERNAL_ERROR);
    }
}
