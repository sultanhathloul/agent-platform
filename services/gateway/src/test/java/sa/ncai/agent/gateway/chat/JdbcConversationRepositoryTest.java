package sa.ncai.agent.gateway.chat;

import static org.assertj.core.api.Assertions.assertThat;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.stream.IntStream;
import org.flywaydb.core.Flyway;
import org.flywaydb.core.api.output.MigrateResult;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import sa.ncai.agent.gateway.chat.ConversationRepository.ConversationOwner;

/**
 * {@link JdbcConversationRepository} and the Flyway migrations against the compose Postgres (ADR-0051).
 * Each run migrates a throwaway database and drops it afterwards. Runs only in {@code make
 * test-gateway-db}; without the connection settings it fails, never skips.
 */
@Tag("db")
class JdbcConversationRepositoryTest {

    private static final String SERVER_URL = required("GATEWAY_TEST_DB_URL"); // e.g. jdbc:postgresql://postgres:5432/
    private static final String USER = required("GATEWAY_TEST_DB_USER");
    private static final String PASSWORD = required("GATEWAY_TEST_DB_PASSWORD");

    private static String database;
    private static DriverManagerDataSource dataSource;
    private static MigrateResult migration;
    private static JdbcClient jdbc;
    private static JdbcConversationRepository repository;

    @BeforeAll
    static void migrateAThrowawayDatabase() throws SQLException {
        database = "gateway_it_" + UUID.randomUUID().toString().replace("-", "");
        adminExecute("CREATE DATABASE " + database);
        dataSource = new DriverManagerDataSource(SERVER_URL + database, USER, PASSWORD);
        // The same Flyway settings as spring.flyway in application.yml.
        migration = Flyway.configure()
                .dataSource(dataSource)
                .schemas("gateway")
                .defaultSchema("gateway")
                .createSchemas(true)
                .load()
                .migrate();
        jdbc = JdbcClient.create(dataSource);
        repository = new JdbcConversationRepository(jdbc);
    }

    @AfterAll
    static void dropTheDatabase() throws SQLException {
        if (database != null) {
            adminExecute("DROP DATABASE IF EXISTS " + database + " WITH (FORCE)");
        }
    }

    @Test
    void migrationsApplyAndCreateTheConversationsTable() {
        assertThat(migration.success).isTrue();
        assertThat(migration.migrations).extracting(m -> m.version).containsExactly("1", "2");
        List<String> columns = jdbc.sql("""
                        SELECT column_name FROM information_schema.columns
                        WHERE table_schema = 'gateway' AND table_name = 'conversations'
                        ORDER BY ordinal_position
                        """)
                .query(String.class)
                .list();
        assertThat(columns).containsExactly("id", "user_id", "tenant_id", "created_at");
    }

    @Test
    void theFirstCallerOwnsANewConversation() {
        String id = UUID.randomUUID().toString();

        assertThat(repository.bindOrGetOwner(id, "user-1", "tenant-a")).isEqualTo(new ConversationOwner("user-1", "tenant-a"));
        assertThat(rows(id)).isEqualTo(1);
    }

    @Test
    void aLaterCallerGetsTheExistingOwnerAndChangesNothing() {
        String id = UUID.randomUUID().toString();
        repository.bindOrGetOwner(id, "user-1", "tenant-a");

        // ON CONFLICT DO NOTHING: another user, then another tenant, read the first owner back.
        assertThat(repository.bindOrGetOwner(id, "user-2", "tenant-a")).isEqualTo(new ConversationOwner("user-1", "tenant-a"));
        assertThat(repository.bindOrGetOwner(id, "user-1", "tenant-b")).isEqualTo(new ConversationOwner("user-1", "tenant-a"));
        assertThat(rows(id)).isEqualTo(1);
    }

    @Test
    void upperAndLowerCaseSpellingsAreOneConversation() {
        String id = UUID.randomUUID().toString();
        repository.bindOrGetOwner(id, "user-1", "tenant-a");

        assertThat(repository.bindOrGetOwner(id.toUpperCase(Locale.ROOT), "user-2", "tenant-a").userId()).isEqualTo("user-1");
        assertThat(rows(id)).isEqualTo(1);
    }

    @Test
    void concurrentFirstTurnsAgreeOnOneOwner() throws Exception {
        String id = UUID.randomUUID().toString();
        int callers = 16;
        ExecutorService pool = Executors.newFixedThreadPool(callers);
        CountDownLatch start = new CountDownLatch(1);
        try {
            List<Future<ConversationOwner>> results = IntStream.range(0, callers)
                    .mapToObj(i -> pool.submit(() -> {
                        start.await();
                        return repository.bindOrGetOwner(id, "user-" + i, "tenant-a");
                    }))
                    .toList();
            start.countDown();
            Set<ConversationOwner> owners = new HashSet<>();
            for (Future<ConversationOwner> result : results) {
                owners.add(result.get(30, TimeUnit.SECONDS));
            }

            assertThat(owners).hasSize(1);
            assertThat(owners.iterator().next().userId()).startsWith("user-");
            assertThat(rows(id)).isEqualTo(1);
        } finally {
            pool.shutdownNow();
        }
    }

    private static int rows(String id) {
        return jdbc.sql("SELECT count(*) FROM gateway.conversations WHERE id = :id::uuid")
                .param("id", id)
                .query(Integer.class)
                .single();
    }

    private static void adminExecute(String sql) throws SQLException {
        try (Connection admin = DriverManager.getConnection(SERVER_URL + "postgres", USER, PASSWORD);
                Statement statement = admin.createStatement()) {
            statement.execute(sql);
        }
    }

    private static String required(String name) {
        String value = System.getenv(name);
        if (value == null || value.isBlank()) {
            throw new IllegalStateException(name + " is not set; run `make test-gateway-db` (ADR-0051)");
        }
        return value;
    }
}
