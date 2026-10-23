# CPU image. Model weights are baked in at build time so the container runs offline.
FROM python:3.13-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
    KS_DATA_DIR=/data KS_MODEL_CACHE=/models KS_OFFLINE=1
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY site2md ./site2md
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev
ARG KS_EMBED_MODEL=BAAI/bge-small-en-v1.5
ENV KS_EMBED_MODEL=${KS_EMBED_MODEL}
RUN KS_OFFLINE=0 /app/.venv/bin/knowledge-search download-model
RUN useradd --system --uid 10001 ks && mkdir -p /data && chown ks /data && chmod -R a+rX /models
USER ks
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["sh", "-c", "/app/.venv/bin/knowledge-search migrate && exec /app/.venv/bin/knowledge-search serve --host 0.0.0.0"]
