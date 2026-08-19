# Unified Dockerfile for Render/cloud deploy (backend + frontend in one container)

# --- Stage 1: Build Frontend ---
FROM node:22-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN npm install -g pnpm && pnpm install --frozen-lockfile
COPY frontend/ ./
ENV VITE_WS_BACKEND_URL=""
ENV VITE_HTTP_BACKEND_URL=""
RUN pnpm build

# --- Stage 2: Backend + serve frontend ---
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1
ENV PORT=7001

# Install system deps for Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libxshmfence1 \
    libx11-xcb1 \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN pip install --no-cache-dir "poetry==2.4.1"

# Backend setup
WORKDIR /app
COPY backend/poetry.lock backend/pyproject.toml ./
RUN poetry config virtualenvs.create false && poetry install --no-interaction --no-ansi --no-root

# Install Playwright Chromium
RUN playwright install chromium

# Copy backend code
COPY backend/ ./

# Copy built frontend
COPY --from=frontend-build /frontend/dist /app/frontend_dist

EXPOSE 7001

CMD uvicorn main:app --host 0.0.0.0 --port ${PORT}
