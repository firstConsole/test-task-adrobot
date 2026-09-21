# AD Robot frontend. The build context is frontend/, the directory this file lives in:
#
#     docker build -t adrobot-web frontend/

ARG NODE_VERSION=26
ARG NGINX_VERSION=1-alpine


# --- builder ----------------------------------------------------------------------------
FROM docker.io/library/node:${NODE_VERSION}-alpine AS builder

WORKDIR /app

# The manifests alone first, so this layer survives every edit under src/. `npm ci` and not
# `npm install`: it installs the lock file and fails if the two have drifted.
COPY package.json package-lock.json ./
RUN npm ci

# src/shared/api/schema.gen.ts is committed, so this build needs neither Python nor the
# OpenAPI document — only the sources that are already here.
COPY . .
RUN npm run build


# --- runtime ----------------------------------------------------------------------------
# The unprivileged image rather than the official one plus a chown dance: it already runs as
# uid 101 with writable cache and pid paths, and it carries the same entrypoint scripts —
# which is what renders the template below.
FROM docker.io/nginxinc/nginx-unprivileged:${NGINX_VERSION} AS runtime

COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 8080

HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=3 \
    CMD ["wget", "--quiet", "--spider", "http://127.0.0.1:8080/healthz"]
