FROM python:3.13-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first for layer caching.
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

# Copy source and channel data.
COPY src/ ./src/
COPY channels/ ./channels/

# data/ is mounted as a volume by docker-compose; do not bake into the image.

CMD ["python", "-m", "channels_operator.main"]
