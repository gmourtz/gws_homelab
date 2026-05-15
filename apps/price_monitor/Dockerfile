FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Install Chromium system deps as root, then browser as appuser
RUN playwright install-deps chromium

RUN useradd --create-home appuser
RUN mkdir -p /data && chown appuser:appuser /data
VOLUME /data

COPY src/ ./src/

USER appuser
RUN playwright install chromium

ENV PYTHONUNBUFFERED=1

CMD ["python", "src/main.py"]
