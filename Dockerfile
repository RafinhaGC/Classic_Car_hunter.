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

# If any updated files were uploaded directly to the repo root on GitHub, sync them into app/ automatically
RUN for f in main.py database.py notifications.py scrapers.py; do [ -f "./$f" ] && cp -f "./$f" "./app/$f" || true; done \
    && [ -f "./index.html" ] && cp -f "./index.html" "./app/static/index.html" || true

ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
