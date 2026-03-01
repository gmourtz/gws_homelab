FROM ubuntu:24.04

RUN apt-get update && apt-get install -y --no-install-recommends \
    sudo git curl jq ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install OpenClaw via official script (installs Node.js v22 + OpenClaw)
RUN curl -fsSL --proto '=https' --tlsv1.2 https://openclaw.ai/install.sh | bash -s -- --install-method git

VOLUME ["/root/.openclaw"]
EXPOSE 18789

CMD ["openclaw", "gateway", "--bind", "lan", "--port", "18789"]
