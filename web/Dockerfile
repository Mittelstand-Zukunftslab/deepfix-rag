# Production image for the TanStack Start web app.
# Build context is web/: docker build -t deepfix-web web

FROM node:22-bookworm-slim AS build

WORKDIR /app

# Bun installs from bun.lock. better-sqlite3 is then rebuilt with Node so the
# native binding matches this image's Node ABI (the server runs on Node).
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 make g++ \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g bun

COPY package.json bun.lock bunfig.toml ./
RUN bun install --frozen-lockfile \
    && npm rebuild better-sqlite3

COPY . .
RUN bun run build

FROM node:22-bookworm-slim AS runtime

WORKDIR /app

ENV NODE_ENV=production \
    HOST=0.0.0.0 \
    PORT=8080 \
    NITRO_HOST=0.0.0.0 \
    NITRO_PORT=8080

COPY --from=build --chown=node:node /app/.output ./.output

# Default the SQLite file onto the /data volume. Compose and `docker run -e`
# can still override AUTH_DATABASE_PATH.
RUN mkdir -p /data \
    && chown node:node /data \
    && printf '%s\n' \
        '#!/bin/sh' \
        'export AUTH_DATABASE_PATH="${AUTH_DATABASE_PATH:-/data/auth.db}"' \
        'exec node .output/server/index.mjs' \
        > /usr/local/bin/deepfix-web \
    && chmod 755 /usr/local/bin/deepfix-web

USER node

EXPOSE 8080

VOLUME /data

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD node -e "fetch('http://127.0.0.1:8080/').then((r)=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["deepfix-web"]
