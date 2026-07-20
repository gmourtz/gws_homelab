FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY schema.sql .
COPY src/ ./src/

RUN useradd --create-home appuser
RUN mkdir -p /data && chown appuser:appuser /data
VOLUME /data

USER appuser

ENV PYTHONUNBUFFERED=1
ENV PORT=8100
ENV HEALTH_DB_PATH=/data/health.db

EXPOSE 8100

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8100/mcp')" || exit 1

CMD ["python", "src/server.py"]
