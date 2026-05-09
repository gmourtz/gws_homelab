FROM nginx:alpine

RUN apk upgrade --no-cache

COPY website/ /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD wget -q --spider http://localhost || exit 1
