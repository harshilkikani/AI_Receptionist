# Dockerfile — AI Receptionist (v3)
#
# Single-stage, small-ish image. Runs the FastAPI app on :8765 inside
# the container. Mount the host's data/ and clients/ into the container
# so state survives restarts and operator edits land without rebuilding.
#
# Build: docker build -t ai-receptionist:v3 .
# Run:   docker run -p 8765:8765 \
#          -v $PWD/data:/app/data \
#          -v $PWD/clients:/app/clients \
#          -v $PWD/agencies:/app/agencies \
#          -v $PWD/logs:/app/logs \
#          --env-file .env \
#          ai-receptionist:v3
#
# Preferred: docker-compose up (see docker-compose.yml).

FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install OS-level deps (minimal — SQLite is in the base image, we
# don't need build tools for our pure-python deps)
RUN apt-get update && apt-get install -y --no-install-recommends \
        tini ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first for better layer caching
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy app source
COPY . .

# Non-root user — runs as UID 1000 to match common host mount ownership
RUN useradd --create-home --uid 1000 app && \
    mkdir -p /app/data /app/logs && \
    chown -R app:app /app
USER app

EXPOSE 8765

# Health probe — uses /health so container orchestrators can tell us
# apart from a process that started but isn't serving requests.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS http://localhost:8765/health || exit 1

# tini handles SIGTERM cleanly so uvicorn shuts down gracefully.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8765"]
