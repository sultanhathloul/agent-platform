package sa.ncai.agent.gateway;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.UncheckedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import sa.ncai.agent.gateway.attachments.ObjectStorage;

/** The uploads bucket in memory; {@code failing} makes every call fail as MinIO being down would. */
public class InMemoryObjectStorage implements ObjectStorage {

    public final Map<String, byte[]> objects = new ConcurrentHashMap<>();
    public volatile boolean failing;

    @Override
    public void put(String key, Path file, long size, String contentType) {
        check(key);
        try {
            objects.put(key, Files.readAllBytes(file));
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    @Override
    public InputStream get(String key) {
        check(key);
        byte[] bytes = objects.get(key);
        if (bytes == null) {
            throw new StorageUnavailableException("no object " + key, null);
        }
        return new ByteArrayInputStream(bytes);
    }

    @Override
    public void delete(String key) {
        check(key);
        objects.remove(key);
    }

    public void clear() {
        objects.clear();
        failing = false;
    }

    private void check(String key) {
        if (failing) {
            throw new StorageUnavailableException("storage down for " + key, null);
        }
    }
}
