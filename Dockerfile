# Multi-stage build für kleineres Image
FROM python:3.11-slim as builder

# Build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
WORKDIR /build
COPY requirements.txt* ./
RUN pip install --user --no-cache-dir peewee flask wtforms schedule intelhex crcmod secrets

# Final stage
FROM python:3.11-slim

# Runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlite3 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create app user
RUN useradd -m -u 1000 appuser && \
    mkdir -p /var/lib/mytracker && \
    chown -R appuser:appuser /var/lib/mytracker

# Copy Python dependencies from builder
COPY --from=builder /root/.local /home/appuser/.local

# Set up application
WORKDIR /app
COPY --chown=appuser:appuser . .

# Create data directory
RUN mkdir -p data && chown -R appuser:appuser data

# Switch to non-root user
USER appuser

# Update PATH
ENV PATH=/home/appuser/.local/bin:$PATH

# Expose web port
EXPOSE 5555

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:5555/ || exit 1

# Start application
CMD ["python", "app.py"]
