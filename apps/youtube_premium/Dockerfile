# Use an official Python runtime as a parent image
FROM python:3.10-slim

# Set the working directory in the container
WORKDIR /app

# Copy the dependencies file and install them
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --upgrade yt-dlp && \
    pip install --no-cache-dir -r requirements.txt && \
    # Drop build-only tooling from the runtime image — not used at runtime (yt-dlp
    # runs as a subprocess; google libs use importlib.metadata, not pkg_resources)
    # and the source of HIGH CVEs flagged by Trivy (setuptools-vendored
    # jaraco.context, wheel). Removing them is the root-cause fix.
    pip uninstall -y setuptools wheel

# Copy the application source code
COPY src/ ./src/

# Install ffmpeg and deno (JS runtime required by yt-dlp)
RUN apt-get update && apt-get install -y ffmpeg curl unzip && \
    curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh && \
    apt-get purge -y curl unzip && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

# Create a non-root user and a directory for videos
RUN useradd --create-home appuser && \
    mkdir /videos && \
    chown appuser:appuser /videos

# Switch to the non-root user
USER appuser

# Set environment variables
ENV HOME=/home/appuser
ENV PYTHONUNBUFFERED=1

# Specify the command to run on container startup
CMD ["python", "src/main.py"]