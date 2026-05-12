# PAE Multi-Stage Build
# Stage 1: Rust engine
# Stage 2: Python analytics
# Stage 3: Final image with UI + all services
#
# Portable: uses APP_ROOT env var for Docker -> VM migration.

# --- Stage 1: Build Rust Engine ---
FROM rust:1.78-slim-bookworm AS engine-builder

WORKDIR /build
COPY engine/ ./engine/
WORKDIR /build/engine
RUN cargo build --release

# --- Stage 2: Python Analytics ---
FROM python:3.12-slim-bookworm AS analytics

WORKDIR /app/analytics
COPY analytics/ ./
RUN pip install --no-cache-dir -e ".[dev]"

# --- Stage 3: Final Runtime ---
FROM python:3.12-slim-bookworm AS runtime

ENV APP_ROOT=/app
ENV PAE_ENGINE_PORT=3001
ENV PAE_UI_PORT=3000

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR ${APP_ROOT}

# Copy Rust binary
COPY --from=engine-builder /build/engine/target/release/pae-engine ./bin/pae-engine

# Copy Python analytics
COPY --from=analytics /app/analytics ./analytics/
COPY --from=analytics /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages

# Copy UI static files
COPY ui/src/ ./ui/

# Copy knowledge templates
COPY knowledge/ ./knowledge/

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:${PAE_ENGINE_PORT}/health || exit 1

EXPOSE ${PAE_ENGINE_PORT} ${PAE_UI_PORT}

# Start engine (UI served separately or via reverse proxy)
CMD ["./bin/pae-engine"]
