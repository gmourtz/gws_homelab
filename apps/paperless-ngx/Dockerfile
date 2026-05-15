# paperless-ngx with the Greek OCR language pack baked in.
#
# The official image installs OCR languages at runtime via apt (driven by the
# PAPERLESS_OCR_LANGUAGES env var). paperless-ngx runs on an `internal: true`
# Docker network (no internet — "documents stay local"), so that apt install
# fails and the container crash-loops on a missing-language SystemCheckError.
# Installing tesseract-ocr-ell at build time (CI has internet) is the fix.
FROM ghcr.io/paperless-ngx/paperless-ngx:latest

# apt-get upgrade applies Debian security updates (clears OS-package CVEs the
# base image ships with). Bundled Python deps are upstream paperless's to patch.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends tesseract-ocr-ell \
    && rm -rf /var/lib/apt/lists/*
