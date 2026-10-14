package sa.ncai.agent.gateway.attachments;

import java.io.IOException;

/** An upload's body did not arrive within its deadline (the verification of review round 2): 408 {@code upload_timeout}. */
public final class UploadDeadlineExceededException extends IOException {

    UploadDeadlineExceededException() {
        super("the upload body did not arrive within its deadline");
    }

    /** True when {@code error} or one of its causes is this exception. */
    public static boolean in(Throwable error) {
        for (Throwable cause = error; cause != null; cause = cause.getCause()) {
            if (cause instanceof UploadDeadlineExceededException) {
                return true;
            }
        }
        return false;
    }
}
