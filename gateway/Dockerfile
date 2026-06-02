# ============================================================
# Dockerfile – MyFeed API Gateway
# Schlankes Python-Image mit FastAPI + Uvicorn
# ============================================================

FROM python:3.12-slim

# Kein .pyc-Bytecode, kein Buffering für saubere Docker-Logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Abhängigkeiten zuerst installieren (Layer-Cache optimieren)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Anwendungscode kopieren
COPY main.py .

# Uvicorn auf Port 8000 starten (gebunden an alle Interfaces im Container)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
