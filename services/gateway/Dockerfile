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
# Gradle's build cache lives in the cache mount; without --no-build-cache `test` is restored FROM-CACHE (ADR-0043).
RUN --mount=type=cache,target=/home/gradle/.gradle \
    gradle cleanTest test --no-daemon --no-build-cache

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
