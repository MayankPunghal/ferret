# CPU image. The search models (the app's defaults: bge-base and the jina reranker) are baked in, so search
# works offline. The optional summary model downloads into /models on first start (~3.2 GB; KS_OFFLINE=1
# forbids it). Alone, the container runs everything with its own PostgreSQL in /data:
#   docker run -d -p 8000:8000 -v ferret-data:/data -v ferret-models:/models ghcr.io/mayankpunghal/ferret-search
# docker-compose.yml runs a separate PostgreSQL, the app and an indexing worker, for a server.
FROM python:3.13-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
    KS_DATA_DIR=/data KS_MODEL_CACHE=/models
# llama.cpp's Linux build (summaries) needs OpenMP
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY site2md ./site2md
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev
RUN /app/.venv/bin/ferret download-model
# models writable by the app, so Settings can download others (a new named volume starts as a copy of these)
RUN useradd --system --uid 10001 ks && mkdir -p /data && chown -R ks /data /models
USER ks
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["sh", "-c", "/app/.venv/bin/ferret migrate && exec /app/.venv/bin/ferret serve --host 0.0.0.0"]
