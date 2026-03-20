# Dockerfile for agent-security-bench sandbox containers
# Based on skill-inject's proven setup, trimmed to essentials

FROM python:3.11-slim

ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG http_proxy
ARG https_proxy

ENV HTTP_PROXY=$HTTP_PROXY
ENV HTTPS_PROXY=$HTTPS_PROXY
ENV http_proxy=$http_proxy
ENV https_proxy=$https_proxy

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    nodejs \
    npm \
    curl \
    zip \
    unzip \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
RUN pip install --no-cache-dir \
    requests \
    flask \
    python-docx \
    python-pptx \
    openpyxl \
    PyPDF2

# npm 稳定性配置
RUN npm config set strict-ssl false \
 && npm config set fetch-retries 5 \
 && npm config set fetch-retry-mintimeout 20000 \
 && npm config set fetch-retry-maxtimeout 120000

# 安装 Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

# entrypoint
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# 非 root 用户
RUN useradd -m -s /bin/bash agent && \
    mkdir -p /workspace && \
    chown -R agent:agent /workspace

WORKDIR /workspace
USER agent

RUN git config --global user.email "agent@sandbox.local" && \
    git config --global user.name "Sandbox Agent" && \
    git config --global init.defaultBranch main

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
