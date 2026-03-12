FROM python:3.11-slim

# Install system dependencies required by Edge Impulse Linux SDK
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY runner.py .

# Models are mounted at runtime via docker-compose volume
# (edge-impulse/models/ → /models)

EXPOSE 8080

CMD ["python", "runner.py"]
