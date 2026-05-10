FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

RUN useradd --create-home appuser
RUN mkdir -p /data && chown appuser:appuser /data
VOLUME /data

USER appuser

ENV PYTHONUNBUFFERED=1

CMD ["python", "src/main.py"]
