package sa.ncai.agent.gateway.api;

import java.util.Locale;
import org.springframework.context.MessageSource;
import org.springframework.stereotype.Component;

/** Resolves an {@link ErrorCode} to its Arabic and English messages. */
@Component
public class ErrorMessages {

    private static final Locale ARABIC = Locale.forLanguageTag("ar");

    private final MessageSource messages;

    public ErrorMessages(MessageSource messages) {
        this.messages = messages;
    }

    public ApiError error(ErrorCode code) {
        String key = "error." + code.code();
        return new ApiError(
                code.code(),
                messages.getMessage(key, null, ARABIC),
                messages.getMessage(key, null, Locale.ENGLISH));
    }
}
