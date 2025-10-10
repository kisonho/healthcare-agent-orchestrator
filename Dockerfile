# syntax=docker/dockerfile:1.7

ARG PYTHON_VERSION=3.11
ARG NODE_VERSION=20

###############################################################################
# Frontend build stage (optional)
###############################################################################
FROM node:${NODE_VERSION}-bookworm AS frontend-builder

WORKDIR /workspace/democlient

# Installing dependencies separately keeps rebuilds faster when sources change.
COPY democlient/package.json democlient/package-lock.json ./
RUN npm ci

COPY democlient/ .

# Default API base URL can be overridden at build time:
# docker build --build-arg VITE_API_BASE_URL=http://localhost:8080 .
ARG VITE_API_BASE_URL="http://localhost:8000"
RUN printf "VITE_API_BASE_URL=%s\n" "$VITE_API_BASE_URL" > .env.production
RUN npm run build

###############################################################################
# Python runtime
###############################################################################
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SCENARIO=default

WORKDIR /app

# System packages required by scientific libs and OpenCV.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        git \
        libgl1 \
        libglib2.0-0 \
        curl \
        ca-certificates \
        gnupg \
    && rm -rf /var/lib/apt/lists/*

# Install Azure CLI for AzureCliCredential support when authenticating to Azure resources.
RUN curl -sL https://aka.ms/InstallAzureCLIDeb | bash

# Copy requirements first for better layer caching.
COPY src/requirements.txt src/scenarios/default/requirements.txt ./src/

RUN pip install --upgrade pip && \
    pip install -r src/requirements.txt

# Copy application sources.
COPY . .

# Copy built frontend assets (if build stage executed successfully).
COPY --from=frontend-builder /workspace/democlient/build /app/src/static/static

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
