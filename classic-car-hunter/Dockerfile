FROM python:3.12-slim

WORKDIR /app

# Install system dependencies required by lxml / curl_cffi
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Sync files whether uploaded to root, app/, or a nested classic-car-hunter/ folder on GitHub
RUN if [ -d "./classic-car-hunter/app" ]; then cp -rf ./classic-car-hunter/app/* ./app/ 2>/dev/null || true; fi \
    && if [ -f "./classic-car-hunter/hunter.db" ]; then cp -f ./classic-car-hunter/hunter.db ./hunter.db 2>/dev/null || true; fi \
    && for f in main.py database.py notifications.py scrapers.py; do [ -f "./$f" ] && cp -f "./$f" "./app/$f" || true; done \
    && [ -f "./index.html" ] && cp -f "./index.html" "./app/static/index.html" || true \
    && [ -f "./app/index.html" ] && cp -f "./app/index.html" "./app/static/index.html" || true

ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
