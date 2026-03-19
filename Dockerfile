FROM python:3.11-slim

# Set environment
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install system dependencies (needed for sentence-transformers, presidio, etc.)
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc g++ && \
    rm -rf /var/lib/apt/lists/*

# Copy dependency definition first (Docker layer caching)
COPY pyproject.toml README.md ./

# Install Python dependencies (without source — uses layer cache)
RUN pip install --no-cache-dir .

# Copy source code
COPY src/ src/
COPY configs/ configs/
COPY scripts/ scripts/

# Re-install to register entry points with source code present
RUN pip install --no-cache-dir .

# Create output directory
RUN mkdir -p /app/output /app/reports

# Pre-download Sentence-BERT model (avoids first-run delay)
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')" 2>/dev/null || true

# Expose API port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import httpx; r = httpx.get('http://localhost:8000/health'); r.raise_for_status()" || exit 1

# Default command: start API server
CMD ["simtest", "serve", "--host", "0.0.0.0", "--port", "8000"]