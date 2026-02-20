FROM python:3.12-slim

WORKDIR /app

# Build deps for scipy/numpy wheels
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc g++ && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

RUN useradd --create-home appuser
# Persistent data directory (mount a Docker volume here)
RUN mkdir -p /data && chown appuser:appuser /data
VOLUME /data

USER appuser

ENV PYTHONUNBUFFERED=1

CMD ["python", "src/main.py"]
