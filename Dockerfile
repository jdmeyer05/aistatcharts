FROM python:3.11-slim

# System basics
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Workdir
WORKDIR /app

# Dependencies
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# App
COPY . .

# Cloud Run expects the app on $PORT
ENV PORT=8080
EXPOSE 8080

# Start Streamlit
CMD ["streamlit", "run", "app.py", "--server.port=8080", "--server.address=0.0.0.0"]
