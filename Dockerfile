# Unified Dockerfile for SnapDeploy (backend + frontend in one container)
# Backend: FastAPI on port 7001
# Frontend: built static files served by the backend

# --- Stage 1: Build Frontend ---
FROM node:22-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN npm install -g pnpm && pnpm install --frozen-lockfile
COPY frontend/ ./
# Point frontend to same-origin backend (proxy handled by production setup)
ENV VITE_WS_BACKEND_URL=""
ENV VITE_HTTP_BACKEND_URL=""
RUN pnpm build

# --- Stage 2: Backend + serve frontend ---
FROM python:3.12-slim-bookworm

ENV POETRY_VERSION=2.4.1
ENV PYTHONUNBUFFERED=1

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN pip install --no-cache-dir "poetry==$POETRY_VERSION"

# Backend setup
WORKDIR /app
COPY backend/poetry.lock backend/pyproject.toml ./
RUN poetry config virtualenvs.create false && poetry install --no-interaction --no-ansi

# Install Playwright Chromium for screenshot preview
RUN playwright install --with-deps chromium

# Copy backend code
COPY backend/ ./

# Copy built frontend into backend static dir
COPY --from=frontend-build /frontend/dist /app/frontend_dist

# Create a startup script that serves both
RUN echo '#!/bin/bash\nuvicorn main:app --host 0.0.0.0 --port ${PORT:-7001}' > /app/start.sh && chmod +x /app/start.sh

EXPOSE 7001

CMD ["/app/start.sh"]
