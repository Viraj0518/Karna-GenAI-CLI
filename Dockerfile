# syntax=docker/dockerfile:1.7

# -----------------------------------------------------------------------------
# Stage 1: builder — compile project + optional-deps into wheels
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Minimal build deps for wheels that may need a C toolchain (e.g. tiktoken).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copy only what's needed for wheel construction.
COPY pyproject.toml README.md LICENSE.md NOTICES.md ./
COPY karna ./karna

# Build wheels for the project + selected extras into /wheels.
# Dev extras intentionally omitted; cron excluded per brief.
RUN pip wheel --wheel-dir /wheels ".[tokens,web,vertex,bedrock]"

# -----------------------------------------------------------------------------
# Stage 2: runtime — slim image with only the installed package + deps
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ARG KARNA_VERSION=0.0.0

# OCI labels — populated at build time by the workflow.
LABEL org.opencontainers.image.title="nellie" \
      org.opencontainers.image.description="Nellie — Karna's AI agent CLI harness" \
      org.opencontainers.image.source="https://github.com/Viraj0518/Karna-GenAI-CLI" \
      org.opencontainers.image.url="https://github.com/Viraj0518/Karna-GenAI-CLI" \
      org.opencontainers.image.licenses="Proprietary" \
      org.opencontainers.image.version="${KARNA_VERSION}"

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    KARNA_HOME=/home/karna/.karna

# Non-root user (uid/gid 1000).
RUN groupadd --system --gid 1000 karna \
    && useradd --system --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin karna

# Copy built wheels from builder and install them in one layer, then purge.
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl \
    && rm -rf /wheels \
    && find /usr/local -depth \
        \( -type d -a \( -name __pycache__ -o -name tests -o -name test \) \) \
        -exec rm -rf '{}' + || true

WORKDIR /workspace
RUN chown -R karna:karna /workspace

USER karna

ENTRYPOINT ["nellie"]
CMD ["--help"]
