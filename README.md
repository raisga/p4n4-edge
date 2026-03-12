# p4n4-edge

> Dockerized **Edge AI stack** — Edge Impulse TinyML inference on the data path.

The Edge stack runs [Edge Impulse](https://edgeimpulse.com/) `.eim` models inside a lightweight Python runner. It subscribes to raw sensor data on MQTT, performs on-device inference, and publishes results back to MQTT and InfluxDB — closing the loop between IoT telemetry and AI-driven decisions.

Attaches to the shared `p4n4-net` Docker bridge network created by [`p4n4-iot`](https://github.com/raisga/p4n4-iot), enabling seamless integration with MQTT, InfluxDB, Node-RED, and the GenAI stack.

Part of the [p4n4](https://github.com/raisga/p4n4) platform — an EdgeAI + GenAI integration platform for IoT deployments.

---

## Table of Contents

- [Architecture](#architecture)
- [Stack Components](#stack-components)
- [Prerequisites](#prerequisites)
- [Getting Started](#getting-started)
- [Project Structure](#project-structure)
- [Edge Impulse Models](#edge-impulse-models)
- [Sensor Data Format](#sensor-data-format)
- [Inference Results](#inference-results)
- [Mock Mode](#mock-mode)
- [Usage](#usage)
- [Default Ports](#default-ports)
- [Network Requirements](#network-requirements)
- [Security Hardening](#security-hardening)
- [Local Overrides](#local-overrides)
- [Integration with p4n4-iot](#integration-with-p4n4-iot)
- [Resources](#resources)
- [License](#license)

---

## Architecture

```
  [p4n4-iot / MQTT / InfluxDB]
           │
           │  (shared p4n4-net bridge)
           ▼
      [ei-runner]         ← Edge Impulse inference runner
       /        \
      ▼           ▼
  [MQTT]      [InfluxDB]  ← publish results + write to ai_events bucket
  inference/  ai_events
  results
```

**Data flow:** The runner subscribes to the `sensors/raw` MQTT topic. For each message it extracts a feature vector, runs inference via the loaded `.eim` model, and publishes the result (label, confidence, anomaly score) to `inference/results`. Results are also written to the `ai_events` InfluxDB bucket for historical analysis and Grafana dashboards.

---

## Stack Components

| Service | Role | Description |
|---------|------|-------------|
| **ei-runner** | Inference Runner | Python-based Edge Impulse runner. Loads `.eim` models, subscribes to raw sensor data on MQTT, runs on-device inference, and publishes results. Falls back to **mock mode** when no model is present. |

---

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) (v20.10+)
- [Docker Compose](https://docs.docker.com/compose/) (v2.0+)
- `p4n4-iot` running (or `p4n4-net` network created manually — see [Network Requirements](#network-requirements))
- *(Optional)* An Edge Impulse `.eim` model file — download from [Edge Impulse Studio](https://studio.edgeimpulse.com) under **Deployment → Linux (x86_64 or AARCH64)**

---

## Getting Started

1. **Clone the repository**

   ```bash
   git clone https://github.com/raisga/p4n4-edge.git
   cd p4n4-edge
   ```

2. **Configure environment variables**

   ```bash
   cp .env.example .env
   # Edit .env — set MQTT credentials to match p4n4-iot .env
   ```

3. **Deploy your model** *(optional — runs in mock mode without one)*

   ```bash
   # Copy your .eim file to edge-impulse/models/
   make deploy-model MODEL=~/Downloads/my-project-linux-aarch64-v5.eim
   # Then set EI_MODEL_FILE=my-project-linux-aarch64-v5.eim in .env
   ```

4. **Ensure `p4n4-net` exists** (skip if p4n4-iot is already running)

   ```bash
   docker network create p4n4-net
   ```

5. **Build and start the stack**

   ```bash
   make up
   # or
   docker compose up -d
   ```

6. **Verify inference is running**

   ```bash
   make test-inference   # sends test data, prints results
   # or check the health endpoint
   curl http://localhost:8080/health
   ```

---

## Project Structure

```
p4n4-edge/
├── docker-compose.yml                  # Edge stack service definitions
├── docker-compose.override.yml.example # Local override template (GPU, standalone)
├── Makefile                            # Convenience commands
├── .env.example                        # Environment template (copy to .env)
├── .gitignore
├── runner/
│   ├── Dockerfile                      # Container image for the inference runner
│   ├── runner.py                       # Inference loop: MQTT → EI model → MQTT + InfluxDB
│   └── requirements.txt                # Python dependencies
├── edge-impulse/
│   └── models/
│       └── .gitkeep                    # Place .eim files here — NEVER commit them
└── scripts/
    ├── selector.sh                     # Interactive service selector
    └── check_env_example.py            # CI: .env.example completeness check
```

---

## Edge Impulse Models

### Obtaining a Model

1. Train a model in [Edge Impulse Studio](https://studio.edgeimpulse.com)
2. Go to **Deployment → Linux (x86_64)** (or AARCH64 for ARM devices)
3. Click **Build** to download the `.eim` file

### Deploying a Model

```bash
# Copy via Makefile helper
make deploy-model MODEL=~/Downloads/my-model-linux-x86_64-v7.eim

# Or copy manually
cp ~/Downloads/my-model-linux-x86_64-v7.eim edge-impulse/models/

# Set the filename in .env
echo "EI_MODEL_FILE=my-model-linux-x86_64-v7.eim" >> .env

# Restart the runner to load the new model
make restart
```

### Model Compatibility

- `.eim` files are standalone executables targeting a specific architecture (x86_64, aarch64, armv7l)
- The runner container uses `python:3.11-slim` on x86_64 by default
- For ARM/Raspberry Pi deployments, use the override file to set the `platform` or base image

---

## Sensor Data Format

Publish JSON to the `sensors/raw` MQTT topic (configurable via `MQTT_TOPIC_INPUT`):

```json
{
  "device": "vibration-sensor-01",
  "values": [1.23, 4.56, 7.89, 0.12, 3.45, 6.78]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `device` | string | Device identifier (used as InfluxDB tag) |
| `values` | array of floats | Feature vector passed to the EI model |

The `values` array must match the feature count expected by your trained model.

---

## Inference Results

The runner publishes JSON to the `inference/results` MQTT topic (configurable via `MQTT_TOPIC_RESULTS`):

```json
{
  "device": "vibration-sensor-01",
  "timestamp": "2026-03-11T12:00:00.000000+00:00",
  "label": "anomaly",
  "confidence": 0.9234,
  "anomaly_score": 0.8712,
  "latency_ms": 18.4,
  "mode": "model"
}
```

| Field | Description |
|-------|-------------|
| `device` | Echoed from the input message |
| `timestamp` | ISO 8601 UTC timestamp of inference |
| `label` | Top classification label from the model |
| `confidence` | Confidence score for the top label (0–1) |
| `anomaly_score` | Anomaly score from the model (0–1; higher = more anomalous) |
| `latency_ms` | Inference latency in milliseconds |
| `mode` | `"model"` when running a real `.eim`, `"mock"` in mock mode |

Results are also written to the `ai_events` InfluxDB bucket with the measurement name `inference_result`.

---

## Mock Mode

When no `.eim` model file is found (or the Edge Impulse SDK cannot be loaded), the runner enters **mock mode** automatically:

- Simulated inference results are generated based on input magnitude
- Labels are sampled from `["idle", "running", "anomaly", "vibration"]`
- Results are published to MQTT and InfluxDB exactly as in model mode
- The `mode` field in the result payload is set to `"mock"`
- A warning is logged at startup

Mock mode lets you test the full pipeline — MQTT → inference → InfluxDB → Grafana — without needing a trained model.

---

## Usage

### Make Commands

```bash
make help             # Show all available commands

make up               # Build and start the full stack
make down             # Stop all services
make restart          # Restart all services
make logs             # Follow logs from all services
make ps               # Show service status
make status           # Colorized status table
make build            # Rebuild the runner image

make start SERVICE=ei-runner   # Start a single service
make stop SERVICE=ei-runner    # Stop a single service

make deploy-model MODEL=path/to/model.eim   # Deploy a .eim model
make test-inference   # Send test data and print results

make clean            # Stop services and remove all data volumes
```

### Checking the Health Endpoint

```bash
curl http://localhost:8080/health
```

```json
{
  "status": "ok",
  "mode": "model",
  "model_file": "/models/model.eim",
  "inference_count": 142,
  "last_inference_at": "2026-03-11T12:00:00+00:00",
  "mqtt_connected": true,
  "influxdb_ok": true,
  "started_at": "2026-03-11T11:55:00+00:00"
}
```

### Sending Sensor Data Manually

```bash
# From host via mosquitto_pub
mosquitto_pub -h localhost -p 1883 -t sensors/raw \
  -m '{"device":"bench-sensor","values":[1.2,3.4,5.6,0.1,2.3,4.5]}'

# From another container on p4n4-net
docker run --rm --network p4n4-net eclipse-mosquitto:2 \
  mosquitto_pub -h p4n4-mqtt -t sensors/raw \
  -m '{"device":"bench-sensor","values":[1.2,3.4,5.6,0.1,2.3,4.5]}'
```

---

## Default Ports

| Service | Port | URL |
|---------|------|-----|
| ei-runner health API | `8080` | <http://localhost:8080/health> |

---

## Network Requirements

This stack attaches to `p4n4-net` as an **external** network. The network must exist before running `docker compose up`.

**Option 1 — Use p4n4-iot (recommended):**

```bash
# In p4n4-iot directory
docker compose up -d
# Then start p4n4-edge
```

**Option 2 — Create network manually:**

```bash
docker network create p4n4-net
docker compose up -d
```

**Option 3 — Use the CLI:**

```bash
p4n4 up --all   # starts all stacks in the correct order
```

---

## Security Hardening

1. **MQTT credentials** — set `MQTT_USER` and `MQTT_PASSWORD` to match your p4n4-iot Mosquitto configuration.

2. **InfluxDB token** — use a scoped token (write-only to `ai_events`) rather than the admin token in production.

3. **Model files** — `.eim` files are excluded from version control by `.gitignore`. Do not commit them.

4. **EI_API_KEY** — only required for Edge Impulse cloud features (e.g., continuous learning). Leave blank for fully offline deployments.

5. **Restrict port exposure** — for production, remove the `8080` host-port binding and access the health endpoint only within `p4n4-net`.

---

## Local Overrides

Use `docker-compose.override.yml` for machine-specific settings (GPU, custom model path, standalone network):

```bash
cp docker-compose.override.yml.example docker-compose.override.yml
# Edit docker-compose.override.yml as needed
docker compose up -d
```

The override file is listed in `.gitignore` and will never be committed.

---

## Integration with p4n4-iot

When running alongside p4n4-iot on the same `p4n4-net` network, services can be referenced by their container names:

| p4n4-iot Service | Address from p4n4-edge |
|------------------|------------------------|
| MQTT Broker | `p4n4-mqtt:1883` |
| InfluxDB | `p4n4-influxdb:8086` |
| Node-RED | `p4n4-node-red:1880` |

**Shared secrets** (must match between stacks — set identical values in both `.env` files):

| Variable | Purpose |
|----------|---------|
| `INFLUXDB_TOKEN` | InfluxDB API token |
| `INFLUXDB_ORG` | InfluxDB organization |
| `INFLUXDB_BUCKET_AI_EVENTS` | InfluxDB bucket for AI events (`ai_events`) |
| `MQTT_USER` / `MQTT_PASSWORD` | MQTT authentication |

**Recommended Node-RED integration:** Subscribe to `inference/results` in a Node-RED flow to route high-anomaly events (anomaly_score > 0.8) to the p4n4-ai stack's Ollama for natural-language explanations.

---

## Resources

- [p4n4 Platform](https://github.com/raisga/p4n4) — umbrella repo and architecture docs
- [p4n4-iot](https://github.com/raisga/p4n4-iot) — IoT stack (MQTT, InfluxDB, Node-RED, Grafana)
- [p4n4-ai](https://github.com/raisga/p4n4-ai) — GenAI stack (Ollama, Letta, n8n)
- [Edge Impulse Studio](https://studio.edgeimpulse.com) — train and export models
- [Edge Impulse Linux SDK](https://docs.edgeimpulse.com/docs/run-inference/linux) — runner documentation
- [Edge Impulse Python SDK](https://pypi.org/project/edge-impulse-linux/) — PyPI package

---

## License

This project is licensed under the [MIT License](LICENSE).
