# PlanMyAgents API container.
#
# Build (from repo root):
#   docker build -f infra/api/Dockerfile -t planmyagents-api:local .
#
# Run with the local docker-compose Postgres (Linux):
#   docker run --rm -p 8000:8000 \
#     --add-host=host.docker.internal:host-gateway \
#     -e PLANMYAGENTS_DISCOVERY_STORE_URL=postgresql://planmyagents:planmyagents@host.docker.internal:55433/planmyagents \
#     -e PLANMYAGENTS_BENCHMARK_STORE_URL=postgresql://planmyagents:planmyagents@host.docker.internal:55433/planmyagents \
#     -e PLANMYAGENTS_VERIFICATION_STORE_URL=postgresql://planmyagents:planmyagents@host.docker.internal:55433/planmyagents \
#     planmyagents-api:local
#
# The image is intentionally small and dependency-pinned. CMD launches uvicorn
# bound to 0.0.0.0:8000 with a single worker; scale with --workers in your
# orchestrator (Railway, Fly, K8s, etc.).

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/apps/api

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY apps/api ./apps/api
COPY packages ./packages
COPY scripts ./scripts

RUN useradd --create-home --uid 10001 planmyagents \
    && chown -R planmyagents:planmyagents /app
USER planmyagents

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl --fail --silent http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "planmyagents_api.web.app:app", "--host", "0.0.0.0", "--port", "8000"]
