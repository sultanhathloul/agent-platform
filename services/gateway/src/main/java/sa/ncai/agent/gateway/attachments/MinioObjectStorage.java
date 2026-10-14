package sa.ncai.agent.gateway.attachments;

import io.minio.GetObjectArgs;
import io.minio.MinioClient;
import io.minio.PutObjectArgs;
import io.minio.RemoveObjectArgs;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import org.springframework.stereotype.Component;
import sa.ncai.agent.gateway.GatewayProperties;

/** MinIO through its Java client; credentials come from the environment, never from code. */
@Component
public class MinioObjectStorage implements ObjectStorage {

    private final MinioClient client;
    private final String bucket;

    public MinioObjectStorage(GatewayProperties properties) {
        GatewayProperties.Storage storage = properties.storage();
        this.client = MinioClient.builder()
                .endpoint(storage.endpoint())
                .credentials(storage.accessKey(), storage.secretKey())
                .build();
        this.bucket = storage.bucket();
    }

    @Override
    public void put(String key, Path file, long size, String contentType) {
        try (InputStream in = Files.newInputStream(file)) {
            client.putObject(PutObjectArgs.builder().bucket(bucket).object(key)
                    .stream(in, size, -1).contentType(contentType).build());
        } catch (Exception e) {
            throw new StorageUnavailableException("cannot store " + key + ": " + e.getClass().getSimpleName(), e);
        }
    }

    @Override
    public InputStream get(String key) {
        try {
            return client.getObject(GetObjectArgs.builder().bucket(bucket).object(key).build());
        } catch (Exception e) {
            throw new StorageUnavailableException("cannot read " + key + ": " + e.getClass().getSimpleName(), e);
        }
    }

    @Override
    public void delete(String key) {
        try {
            client.removeObject(RemoveObjectArgs.builder().bucket(bucket).object(key).build());
        } catch (Exception e) {
            throw new StorageUnavailableException("cannot delete " + key + ": " + e.getClass().getSimpleName(), e);
        }
    }
}
