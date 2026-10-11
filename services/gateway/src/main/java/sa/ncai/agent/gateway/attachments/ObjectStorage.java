package sa.ncai.agent.gateway.attachments;

import java.io.InputStream;
import java.nio.file.Path;

/** The uploads bucket in MinIO (ADR-0129); internal to the compose network. */
public interface ObjectStorage {

    /** @throws StorageUnavailableException when the object cannot be written */
    void put(String key, Path file, long size, String contentType);

    /** @throws StorageUnavailableException when the object cannot be read */
    InputStream get(String key);

    /** Idempotent: an object already gone counts as deleted. */
    void delete(String key);

    class StorageUnavailableException extends RuntimeException {
        public StorageUnavailableException(String message, Throwable cause) {
            super(message, cause);
        }
    }
}
