# PyAudio, which the Edge Impulse SDK imports, has no Linux wheels: compile it
# against PortAudio here, so the compiler stays out of the final image
FROM python:3.11-slim AS build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libc6-dev \
    portaudio19-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

FROM python:3.11-slim

# libgomp1: ONNX Runtime; libportaudio2: PyAudio
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    libportaudio2 \
    wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir --no-index /wheels/*.whl && rm -rf /wheels

# Fail the build, not the runner, when a backend can't import: the runner
# would otherwise only find out when it loads a model
RUN python -c "import edge_impulse_linux.runner, onnxruntime, numpy, influxdb_client, paho.mqtt.client"

COPY runner.py .

# Models are mounted at runtime via docker-compose volume
# (edge-impulse/models/ → /models)

EXPOSE 8080

CMD ["python", "runner.py"]
