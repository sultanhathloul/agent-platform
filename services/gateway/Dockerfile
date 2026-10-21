# syntax=docker/dockerfile:1.7
# Targets: build (jar), test (fails on any failing test), runtime (default).

FROM gradle:9.7.1-jdk21 AS build
WORKDIR /src
COPY settings.gradle.kts build.gradle.kts gradle.properties ./
RUN --mount=type=cache,target=/home/gradle/.gradle \
    gradle dependencies --no-daemon -q > /dev/null
COPY src ./src
RUN --mount=type=cache,target=/home/gradle/.gradle \
    gradle bootJar --no-daemon -x test

FROM build AS test
# The contract drift test compares the generated spec with the committed one; `make test-gateway`
# passes the repo's docs/ as the named build context `docs` (ADR-0044).
COPY --from=docs openapi/gateway.json /docs/openapi/gateway.json
ENV GATEWAY_OPENAPI_DOC=/docs/openapi/gateway.json
# Role tier caps come from the repo's config/models.yaml, passed as the build context `config` (ADR-0046).
COPY --from=config models.yaml /config/models.yaml
ENV GATEWAY_MODELS_CONFIG=/config/models.yaml
# Upload limits come from the repo's config/docs.yaml (ADR-0126).
COPY --from=config docs.yaml /config/docs.yaml
ENV GATEWAY_DOCS_CONFIG=/config/docs.yaml
# The relay's bound on a turn stream comes from the repo's config/turn.yaml (ADR-0256).
COPY --from=config turn.yaml /config/turn.yaml
ENV GATEWAY_TURN_CONFIG=/config/turn.yaml
# Gradle's build cache lives in the cache mount; without --no-build-cache `test` is restored FROM-CACHE (ADR-0043).
# The JUnit XML totals are printed whether the tests pass or not (phase 03 rule G1).
COPY junit-totals.sh ./
RUN --mount=type=cache,target=/home/gradle/.gradle \
    gradle cleanTest test --no-daemon --no-build-cache; status=$?; \
    sh junit-totals.sh gateway build/test-results/test; exit $status

FROM build AS db-test
# JDBC tests need the compose Postgres, which an image build cannot reach, so they run as a container
# in the compose `test` profile (`make test-gateway-db`, ADR-0051).
COPY junit-totals.sh ./
CMD ["sh", "-c", "gradle dbTest --no-daemon --no-build-cache; status=$?; sh junit-totals.sh gateway-db-tests build/test-results/dbTest; exit $status"]

FROM eclipse-temurin:21.0.12_8-jre AS runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app \
    && useradd --system --gid app --home-dir /app --no-create-home app
WORKDIR /app
COPY --from=build /src/build/libs/gateway.jar /app/gateway.jar
USER app
EXPOSE 8080
ENTRYPOINT ["java", "-XX:MaxRAMPercentage=75", "-jar", "/app/gateway.jar"]
