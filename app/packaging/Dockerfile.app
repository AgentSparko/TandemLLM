# TandemApp, the dashboard and the OpenAI-compatible proxy, as an image: no engine, no GPU, no torch.
# The build context is app/ (packaging/Dockerfile.app.dockerignore says what goes in).
#
#   docker build -f app/packaging/Dockerfile.app -t tandemllm-app app     (from the repository's root)
#   docker run -d --name tandem-app -p 127.0.0.1:8080:8080 -v tandem-app:/data \
#       --env-file app.env tandemllm-app
#
# Everything the proxy keeps (the config database, the usage ledger, the key file when no
# TANDEM_SECRETS_KEY is given) lives in the /data volume. The server binds 0.0.0.0 inside the
# container; what is reachable from outside is what `-p` publishes. Publish it to 127.0.0.1 or a
# trusted LAN address, never to the internet: with no TANDEM_ADMIN_TOKEN set every route is open
# (docs/proxy.md).
#
# The System view's host collector sees the container, not the machine. For the machine and the
# processes behind backends on it, run the container with `--pid=host` (and `--gpus all` for
# NVML), or run `tandem-app hostcollect` on that machine and point the backend's host at it.
#
# The page is built here, in a Node stage (OPS-246: dashboard/dist is not in git): `npm ci` with the
# lockfile, then `npm run build` (type check and Vite). The wheel stage records its manifest
# (dashboard/dist/bundle.json, the bundle's content hash) and the build backend checks it.

FROM node:22.12.0-bookworm-slim AS page
WORKDIR /src/dashboard
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY dashboard ./
RUN npm run build

FROM python:3.12-slim AS build
WORKDIR /src
RUN pip install --no-cache-dir build==1.3.0
COPY pyproject.toml VERSION MANIFEST.in LICENSE COMMERCIAL-LICENSE.md NOTICE ./
COPY docs/proxy.md docs/proxy.md
COPY proxy proxy
COPY chatd chatd
COPY tandemapp tandemapp
COPY packages packages
COPY packaging/tandemapp_build.py packaging/tandemapp_build.py
COPY tools/dashboard_bundle.py tools/dashboard_bundle.py
COPY --from=page /src/dashboard/package-lock.json dashboard/package-lock.json
COPY --from=page /src/dashboard/dist dashboard/dist
RUN python tools/dashboard_bundle.py record --source docker \
    && python tools/dashboard_bundle.py verify \
    && python -m build --wheel -o /wheel .

FROM python:3.12-slim
RUN useradd --create-home --uid 10001 tandem && mkdir -p /data && chown tandem /data
COPY --from=build /wheel/*.whl /tmp/
RUN pip install --no-cache-dir "$(ls /tmp/*.whl)[host]" && rm /tmp/*.whl
USER tandem
ENV TANDEM_CONFIG_DB=/data/config.sqlite3 \
    TANDEM_USAGE_LEDGER=/data/ledger.sqlite3 \
    TANDEM_HOST=0.0.0.0 \
    TANDEM_PORT=8080 \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8080/health', timeout=4)"
STOPSIGNAL SIGTERM
ENTRYPOINT ["tandem-app"]
CMD ["serve", "--no-keychain"]
