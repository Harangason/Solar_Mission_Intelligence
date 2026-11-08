FROM node:22-bookworm-slim AS frontend
WORKDIR /work/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm exec vite build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt "gunicorn>=23,<25"
COPY . ./
COPY --from=frontend /work/web/dist /app/web/dist
RUN mkdir -p /app/storage/data /app/storage/logs
ENV SOLAR_SYSTEM_STORAGE_DIR=/app/storage
EXPOSE 5001
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT:-5001} --workers 1 --threads 4 --timeout 180 main:app"]
