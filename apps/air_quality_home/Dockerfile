FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

RUN useradd --create-home appuser
# Persistent data directory (SQLite DB) — mount a Docker volume here
RUN mkdir -p /data && chown appuser:appuser /data
VOLUME /data

USER appuser

ENV PYTHONUNBUFFERED=1 \
    DATA_DIR=/data

# Default command runs the collector.
# The dashboard service overrides this with `command:` in the compose stack.
CMD ["python", "src/collect_data.py"]
