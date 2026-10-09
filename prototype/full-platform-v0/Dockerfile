# TinyAssets gateway (prototype v0) — FastMCP-over-HTTP on top of the
# shared Postgres + pgvector migrations in this prototype.
#
# Build:   docker build -t tinyassets-v0-gateway .
# Run:     docker compose up -d  (picks this up via docker-compose.yml)
#
# Throwaway prototype container; real track-C build pulls from a proper
# registry + runs on Fly.io Machines per spec #27 §6.

FROM mirror.gcr.io/library/python:3.11-slim

WORKDIR /app

# System deps — psycopg needs libpq; pgvector's Python bindings don't need
# anything extra but build-essential stays out for smaller image.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY gateway.py migrate.py ./
COPY migrations ./migrations

# Default DSN — compose overrides via env for container-to-container
# routing (postgres hostname resolves inside the compose network).
ENV TINYASSETS_V0_DSN="postgresql://tinyassets:tinyassets_v0_dev@postgres:5432/tinyassets_v0"

# FastMCP stdio is the default transport; for HTTP / streamable, real
# track C uses `mcp.run(transport='streamable-http')`. v0 defaults to
# stdio — use `docker compose run gateway` to invoke interactively, or
# override the CMD for HTTP:
#   CMD ["python", "-c", "from gateway import mcp; mcp.run(transport='streamable-http', host='0.0.0.0', port=8002)"]
CMD ["python", "gateway.py"]

EXPOSE 8002
