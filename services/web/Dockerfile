# syntax=docker/dockerfile:1.7
ARG NODE_IMAGE=node:22.23.2-alpine

FROM ${NODE_IMAGE} AS deps
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund

FROM deps AS source
COPY . .

# `docker build --target test` fails when typecheck, lint or unit tests fail.
# The config editors' round-trip tests read the seed policies from the `config` build context
# (ADR-0123).
FROM source AS test
COPY --from=config policies /config/policies
ENV SEED_POLICIES_DIR=/config/policies
RUN npm run typecheck && npm run lint && npm test

FROM source AS build
ENV NODE_OPTIONS=--max-old-space-size=2048
RUN npm run build

FROM ${NODE_IMAGE} AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
COPY --from=build --chown=node:node /app/public ./public
USER node
EXPOSE 3000
CMD ["node", "server.js"]
