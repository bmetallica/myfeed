FROM python:3.12-slim

WORKDIR /app

# fastembed lädt das Modell beim ersten Start herunter → Cache-Volume einbinden
ENV FASTEMBED_CACHE_PATH=/models

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY worker.py .

CMD ["python", "worker.py"]
