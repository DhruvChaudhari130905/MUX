# Sandbox image for the coder's builds and tests (mux/server/mux/sandbox/docker.py).
# Build it with `make sandbox-image`. Each run unpacks the room's files over /app, then runs
# `mux-report build|test ...` with no network, so every package a project can use is installed here.
FROM node:22-bookworm-slim

WORKDIR /app

COPY package.json ./
RUN npm install --no-audit --no-fund && npm cache clean --force

COPY tsconfig.json ./
COPY mux-report.mjs /usr/local/bin/mux-report
RUN chmod 755 /usr/local/bin/mux-report && chown -R node:node /app

ENV PATH=/app/node_modules/.bin:$PATH
USER node
