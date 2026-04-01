FROM python:3.11-slim

# System basics
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# System deps: curl (healthcheck), build-essential (C extensions for scipy/sklearn/shap)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies (cached layer — only rebuilds when requirements.txt changes)
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# App
COPY . .

# Create data directories for caches
RUN mkdir -p /app/data/gdelt_events /app/src

# Ports: Streamlit (8080) + FastAPI (8000)
EXPOSE 8080 8000

# Streamlit config for production
ENV STREAMLIT_SERVER_PORT=8080 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_SERVER_FILE_WATCHER_TYPE=none \
    STREAMLIT_SERVER_MAX_UPLOAD_SIZE=10

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:8080/_stcore/health || curl -f http://localhost:8000/api/health || exit 1

# Start both Streamlit and FastAPI
# Streamlit runs in background, FastAPI in foreground (so Docker tracks its PID)
CMD bash -c "streamlit run app.py \
    --server.port=8080 \
    --server.address=0.0.0.0 \
    --server.enableXsrfProtection=true \
    --server.enableCORS=false &\
    uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 2"
