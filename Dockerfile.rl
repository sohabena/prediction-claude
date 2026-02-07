# ============================================================
# PHOENIX RL Trainer - Multi-stage Docker Build
# Stable-Baselines3 + PyTorch + Gymnasium
# ============================================================

# Stage 1: Build dependencies
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Stage 2: Production runtime
FROM python:3.11-slim AS runtime

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY shared/ /app/shared/
COPY rl/ /app/rl/
COPY features/ /app/features/
COPY virtual_trading/ /app/virtual_trading/

# Create directories for models and logs
RUN mkdir -p /app/models/checkpoints /app/logs

# Create non-root user
RUN useradd --create-home phoenix && \
    chown -R phoenix:phoenix /app/models /app/logs
USER phoenix

# Volumes for model persistence and training logs
VOLUME ["/app/models", "/app/logs"]

CMD ["python", "-m", "rl.trainer"]
