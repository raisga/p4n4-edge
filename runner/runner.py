#!/usr/bin/env python3
"""
p4n4-edge — Edge AI Inference Runner

Loads an Edge Impulse .eim model or an ONNX model, subscribes to raw sensor
data on MQTT, runs inference for each message, and publishes results back to
MQTT and InfluxDB (ai_events bucket).

When no model is found, the runner operates in mock mode and generates
simulated inference results so the full pipeline can be tested end-to-end.

Environment variables (see .env.example):
  MODEL_BACKEND       Backend selection: auto | eim | onnx | mock (default: auto)
  EI_MODEL_PATH       Path to the .eim model file (default: /models/model.eim)
  EI_API_KEY          Edge Impulse API key (optional, for cloud features)
  ONNX_MODEL_PATH     Path to the .onnx model file (default: /onnx-models/model.onnx)
  ONNX_LABELS         Comma-separated class labels for ONNX output (optional)
  MQTT_HOST           MQTT broker hostname (default: p4n4-mqtt)
  MQTT_PORT           MQTT broker port (default: 1883)
  MQTT_USER           MQTT username (optional)
  MQTT_PASSWORD       MQTT password (optional)
  MQTT_TOPIC_INPUT    Topic to subscribe for raw sensor data (default: sensors/raw)
  MQTT_TOPIC_RESULTS  Topic to publish inference results (default: inference/results)
  INFLUXDB_URL        InfluxDB URL (default: http://p4n4-influxdb:8086)
  INFLUXDB_TOKEN      InfluxDB API token
  INFLUXDB_ORG        InfluxDB organization
  INFLUXDB_BUCKET     InfluxDB bucket for AI events (default: ai_events)
  TZ                  Timezone (default: UTC)
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
import subprocess
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt

try:
    from influxdb_client import InfluxDBClient, Point, WritePrecision
    from influxdb_client.client.write_api import SYNCHRONOUS

    HAS_INFLUXDB = True
except ImportError:
    HAS_INFLUXDB = False

try:
    from edge_impulse_linux.runner import ImpulseRunner

    HAS_EI_SDK = True
except ImportError:
    HAS_EI_SDK = False

try:
    import numpy as np
    import onnxruntime as ort

    HAS_ONNX = True
except ImportError:
    HAS_ONNX = False


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MODEL_BACKEND = os.environ.get("MODEL_BACKEND", "auto").lower()
MODEL_PATH = os.environ.get("EI_MODEL_PATH", "/models/model.eim")
ONNX_MODEL_PATH = os.environ.get("ONNX_MODEL_PATH", "/onnx-models/model.onnx")
ONNX_LABELS = [s.strip() for s in os.environ.get("ONNX_LABELS", "").split(",") if s.strip()]
MQTT_HOST = os.environ.get("MQTT_HOST", "p4n4-mqtt")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USER", "")
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD", "")
MQTT_TOPIC_INPUT = os.environ.get("MQTT_TOPIC_INPUT", "sensors/raw")
MQTT_TOPIC_RESULTS = os.environ.get("MQTT_TOPIC_RESULTS", "inference/results")
INFLUXDB_URL = os.environ.get("INFLUXDB_URL", "http://p4n4-influxdb:8086")
INFLUXDB_TOKEN = os.environ.get("INFLUXDB_TOKEN", "")
INFLUXDB_ORG = os.environ.get("INFLUXDB_ORG", "ming")
INFLUXDB_BUCKET = os.environ.get("INFLUXDB_BUCKET", "ai_events")
HEALTH_PORT = int(os.environ.get("HEALTH_PORT", "8080"))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("ei-runner")


# ---------------------------------------------------------------------------
# State shared between threads
# ---------------------------------------------------------------------------

_state: dict[str, Any] = {
    "mode": "starting",      # "mock" | "model" | "onnx" | "starting"
    "model_file": MODEL_PATH,
    "inference_count": 0,
    "last_inference_at": None,
    "mqtt_connected": False,
    "influxdb_ok": False,
    "started_at": datetime.now(timezone.utc).isoformat(),
}
_runner: ImpulseRunner | None = None
_onnx_session = None
_onnx_input_name: str | None = None
_influx_write_api = None
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Health HTTP server
# ---------------------------------------------------------------------------

class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/health", "/"):
            body = json.dumps(
                {
                    "status": "ok",
                    **_state,
                },
                indent=2,
                default=str,
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: ANN401
        pass  # suppress HTTP access logs


def _start_health_server() -> None:
    server = HTTPServer(("0.0.0.0", HEALTH_PORT), _HealthHandler)
    log.info("Health endpoint listening on http://0.0.0.0:%d/health", HEALTH_PORT)
    server.serve_forever()


# ---------------------------------------------------------------------------
# InfluxDB helpers
# ---------------------------------------------------------------------------

def _init_influxdb() -> bool:
    global _influx_write_api  # noqa: PLW0603

    if not HAS_INFLUXDB or not INFLUXDB_TOKEN:
        log.warning("InfluxDB client not available or token not set — skipping writes")
        return False

    try:
        client = InfluxDBClient(url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG)
        _influx_write_api = client.write_api(write_options=SYNCHRONOUS)
        # Probe with a dummy write to verify connectivity
        _influx_write_api.write(
            bucket=INFLUXDB_BUCKET,
            record=Point("runner_start")
            .tag("mode", _state["mode"])
            .field("version", 1)
            .time(datetime.now(timezone.utc), WritePrecision.SECONDS),
        )
        log.info("InfluxDB connected: %s / bucket=%s", INFLUXDB_URL, INFLUXDB_BUCKET)
        return True
    except Exception as exc:
        log.warning("InfluxDB unavailable (%s) — inference results will not be persisted", exc)
        return False


def _write_result_to_influxdb(result: dict) -> None:
    if _influx_write_api is None:
        return
    try:
        point = (
            Point("inference_result")
            .tag("device", result.get("device", "unknown"))
            .tag("label", result.get("label", "unknown"))
            .tag("mode", result.get("mode", "unknown"))
            .field("confidence", float(result.get("confidence", 0.0)))
            .field("anomaly_score", float(result.get("anomaly_score", 0.0)))
            .field("latency_ms", float(result.get("latency_ms", 0.0)))
            .time(datetime.now(timezone.utc), WritePrecision.SECONDS)
        )
        _influx_write_api.write(bucket=INFLUXDB_BUCKET, record=point)
    except Exception as exc:
        log.debug("InfluxDB write failed: %s", exc)


# ---------------------------------------------------------------------------
# Edge Impulse model inference
# ---------------------------------------------------------------------------

def _load_model() -> bool:
    """Try to load the .eim model. Returns True on success."""
    global _runner  # noqa: PLW0603

    model_file = Path(MODEL_PATH)
    if not model_file.exists():
        log.warning(
            "Edge Impulse model not found at %s — place a .eim file in "
            "edge-impulse/models/ and set EI_MODEL_FILE.",
            MODEL_PATH,
        )
        return False

    if not HAS_EI_SDK:
        log.warning(
            "edge_impulse_linux SDK not available — cannot load .eim model. "
            "This is unexpected inside the container; check the image build."
        )
        return False

    try:
        log.info("Loading Edge Impulse model: %s", MODEL_PATH)
        _runner = ImpulseRunner(MODEL_PATH)
        model_info = _runner.init()
        log.info(
            "Model loaded: %s (DSP=%dms, classification=%dms, anomaly=%dms)",
            model_info.get("project", {}).get("name", "unknown"),
            model_info.get("model_parameters", {}).get("dsp_block_execution_time_us", 0) // 1000,
            model_info.get("model_parameters", {}).get("inferencing_time_us", 0) // 1000,
            model_info.get("model_parameters", {}).get("anomaly_inferencing_time_us", 0) // 1000,
        )
        return True
    except Exception as exc:
        log.error("Failed to load model: %s — falling back to MOCK mode", exc)
        _runner = None
        return False


# ---------------------------------------------------------------------------
# ONNX model inference
# ---------------------------------------------------------------------------

def _load_onnx_model() -> bool:
    """Try to load the .onnx model. Returns True on success."""
    global _onnx_session, _onnx_input_name  # noqa: PLW0603

    model_file = Path(ONNX_MODEL_PATH)
    if not model_file.exists():
        log.warning(
            "ONNX model not found at %s — place a .onnx file in onnx/models/ "
            "and set ONNX_MODEL_FILE.",
            ONNX_MODEL_PATH,
        )
        return False

    if not HAS_ONNX:
        log.warning(
            "onnxruntime not available — cannot load ONNX model. "
            "This is unexpected inside the container; check the image build."
        )
        return False

    try:
        log.info("Loading ONNX model: %s", ONNX_MODEL_PATH)
        session = ort.InferenceSession(ONNX_MODEL_PATH, providers=["CPUExecutionProvider"])
        inp = session.get_inputs()[0]
        log.info("ONNX model loaded: input '%s' shape=%s", inp.name, inp.shape)
        _onnx_session = session
        _onnx_input_name = inp.name
        return True
    except Exception as exc:
        log.error("Failed to load ONNX model: %s", exc)
        _onnx_session = None
        _onnx_input_name = None
        return False


def _flatten_scores(output: Any) -> list[float]:
    """Flatten an ONNX output (ndarray or nested lists) to a flat float list."""
    if hasattr(output, "flatten"):  # numpy ndarray
        return [float(v) for v in output.flatten()]
    if isinstance(output, (list, tuple)):
        flat: list[float] = []
        for item in output:
            flat.extend(_flatten_scores(item))
        return flat
    return [float(output)]


def _postprocess_onnx_output(output: Any) -> tuple[str, float]:
    """Turn the first ONNX output into a (label, confidence) pair.

    Handles the two common classifier shapes: a probability map (e.g.
    sklearn-onnx ZipMap output: [{label: prob, ...}]) and a raw score
    array (softmax applied when scores are not already probabilities).
    """
    # ZipMap-style output: list of {label: prob} dicts, one per batch row
    if isinstance(output, (list, tuple)) and output and isinstance(output[0], dict):
        probs = {str(k): float(v) for k, v in output[0].items()}
    else:
        scores = _flatten_scores(output)
        if not scores:
            return "unknown", 0.0
        if any(s < 0.0 for s in scores) or not math.isclose(sum(scores), 1.0, abs_tol=1e-3):
            m = max(scores)
            exps = [math.exp(s - m) for s in scores]
            total = sum(exps)
            scores = [e / total for e in exps]
        probs = {
            ONNX_LABELS[i] if i < len(ONNX_LABELS) else f"class_{i}": s
            for i, s in enumerate(scores)
        }

    label, confidence = max(probs.items(), key=lambda kv: kv[1], default=("unknown", 0.0))
    return label, confidence


def _run_inference(values: list[float]) -> dict:
    """Run inference on the given feature vector. Returns a result dict."""
    t0 = time.monotonic()

    if _onnx_session is not None:
        try:
            x = np.asarray(values, dtype=np.float32).reshape(1, -1)
            outputs = _onnx_session.run(None, {_onnx_input_name: x})
            latency_ms = (time.monotonic() - t0) * 1000

            label, confidence = _postprocess_onnx_output(outputs[0])

            return {
                "label": label,
                "confidence": round(confidence, 4),
                "anomaly_score": 0.0,
                "latency_ms": round(latency_ms, 2),
                "mode": "onnx",
            }
        except Exception as exc:
            log.warning("ONNX inference error: %s", exc)

    if _runner is not None:
        try:
            result = _runner.classify(values)
            latency_ms = (time.monotonic() - t0) * 1000

            # Extract top classification label
            classification = result.get("result", {}).get("classification", {})
            label, confidence = max(
                classification.items(), key=lambda kv: kv[1], default=("unknown", 0.0)
            )
            anomaly_score = result.get("result", {}).get("anomaly", 0.0)

            return {
                "label": label,
                "confidence": round(confidence, 4),
                "anomaly_score": round(anomaly_score, 4),
                "latency_ms": round(latency_ms, 2),
                "mode": "model",
            }
        except Exception as exc:
            log.warning("Inference error: %s", exc)

    # --- Mock mode ---
    latency_ms = (time.monotonic() - t0) * 1000 + random.uniform(5, 25)

    # Simulate a plausible anomaly score based on input magnitude
    magnitude = math.sqrt(sum(v**2 for v in values) / max(len(values), 1)) if values else 0.0
    anomaly_score = min(1.0, magnitude / 10.0 + random.gauss(0, 0.05))
    anomaly_score = max(0.0, anomaly_score)

    labels = ["idle", "running", "anomaly", "vibration"]
    weights = [0.5, 0.3, 0.1, 0.1]
    label = random.choices(labels, weights=weights, k=1)[0]
    confidence = round(random.uniform(0.70, 0.99), 4)

    return {
        "label": label,
        "confidence": confidence,
        "anomaly_score": round(anomaly_score, 4),
        "latency_ms": round(latency_ms, 2),
        "mode": "mock",
    }


# ---------------------------------------------------------------------------
# MQTT callbacks
# ---------------------------------------------------------------------------

def _on_connect(client: mqtt.Client, userdata: Any, flags: dict, rc: int, props=None) -> None:
    if rc == 0:
        with _lock:
            _state["mqtt_connected"] = True
        log.info("MQTT connected to %s:%d", MQTT_HOST, MQTT_PORT)
        client.subscribe(MQTT_TOPIC_INPUT)
        log.info("Subscribed to topic: %s", MQTT_TOPIC_INPUT)
    else:
        log.error("MQTT connection failed (rc=%d)", rc)


def _on_disconnect(client: mqtt.Client, userdata: Any, rc: int, props=None) -> None:
    with _lock:
        _state["mqtt_connected"] = False
    log.warning("MQTT disconnected (rc=%d) — will reconnect", rc)


def _on_message(client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
    try:
        payload = json.loads(msg.payload.decode())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        log.warning("Ignored malformed message on %s: %s", msg.topic, exc)
        return

    device = payload.get("device", "unknown")
    values: list[float] = payload.get("values", [])

    if not isinstance(values, list) or not values:
        log.debug("Message from %s has no 'values' array — skipping", device)
        return

    inference = _run_inference(values)

    result = {
        "device": device,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **inference,
    }

    # Publish to MQTT
    client.publish(MQTT_TOPIC_RESULTS, json.dumps(result))

    # Write to InfluxDB
    _write_result_to_influxdb(result)

    with _lock:
        _state["inference_count"] += 1
        _state["last_inference_at"] = result["timestamp"]

    log.info(
        "device=%-16s  label=%-12s  confidence=%.2f  anomaly=%.2f  latency=%.1fms  [%s]",
        device,
        inference["label"],
        inference["confidence"],
        inference["anomaly_score"],
        inference["latency_ms"],
        inference["mode"],
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _load_backend() -> str:
    """Load the configured model backend. Returns the resulting mode.

    MODEL_BACKEND=auto tries Edge Impulse first, then ONNX; "eim" and
    "onnx" force a single backend; anything else (e.g. "mock") skips
    model loading entirely. Falls back to mock mode when loading fails.
    """
    if MODEL_BACKEND in ("auto", "eim") and _load_model():
        return "model"
    if MODEL_BACKEND in ("auto", "onnx") and _load_onnx_model():
        return "onnx"
    return "mock"


def main() -> None:
    log.info("p4n4-edge — Edge AI Inference Runner starting (backend=%s)", MODEL_BACKEND)

    # Start health server in background
    threading.Thread(target=_start_health_server, daemon=True).start()

    # Load model
    mode = _load_backend()
    with _lock:
        _state["mode"] = mode
        _state["model_file"] = ONNX_MODEL_PATH if mode == "onnx" else MODEL_PATH

    if mode == "mock":
        log.info("Running in MOCK mode — simulated inference results will be published")

    # Connect to InfluxDB
    influx_ok = _init_influxdb()
    with _lock:
        _state["influxdb_ok"] = influx_ok

    # Connect to MQTT
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if MQTT_USER:
        client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
    client.on_connect = _on_connect
    client.on_disconnect = _on_disconnect
    client.on_message = _on_message

    log.info("Connecting to MQTT broker at %s:%d", MQTT_HOST, MQTT_PORT)

    while True:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
            break
        except OSError as exc:
            log.warning("MQTT connection failed (%s) — retrying in 5s", exc)
            time.sleep(5)

    log.info(
        "Ready. Listening on '%s' → publishing to '%s'",
        MQTT_TOPIC_INPUT,
        MQTT_TOPIC_RESULTS,
    )

    client.loop_forever()


if __name__ == "__main__":
    main()
