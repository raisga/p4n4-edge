"""
Unit tests for runner/runner.py.

No Docker, MQTT broker, or InfluxDB required — all external dependencies
are mocked by conftest.py and unittest.mock.
"""

from __future__ import annotations

import importlib
import json
import sys
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Make sure the runner package is importable from this repo layout
_RUNNER_DIR = Path(__file__).parent.parent / "runner"
if str(_RUNNER_DIR) not in sys.path:
    sys.path.insert(0, str(_RUNNER_DIR))

import runner as R  # noqa: E402  (conftest stubs must load first)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mqtt_message(payload: object, topic: str = "sensors/raw") -> MagicMock:
    """Build a fake paho MQTTMessage-like object."""
    msg = MagicMock()
    msg.topic = topic
    if isinstance(payload, (dict, list)):
        msg.payload = json.dumps(payload).encode()
    else:
        msg.payload = payload
    return msg


def _reset_globals():
    """Reset mutable module-level state between tests."""
    R._runner = None
    R._influx_write_api = None
    R._state.update(
        {
            "mode": "mock",
            "inference_count": 0,
            "last_inference_at": None,
            "mqtt_connected": False,
            "influxdb_ok": False,
        }
    )


@pytest.fixture(autouse=True)
def clean_state():
    _reset_globals()
    yield
    _reset_globals()


# ---------------------------------------------------------------------------
# _run_inference — mock mode (no .eim loaded)
# ---------------------------------------------------------------------------

class TestMockInference:
    def test_returns_required_keys(self):
        result = R._run_inference([1.0, 2.0, 3.0])
        for key in ("label", "confidence", "anomaly_score", "latency_ms", "mode"):
            assert key in result, f"Missing key: {key}"

    def test_mode_is_mock(self):
        result = R._run_inference([1.0, 2.0])
        assert result["mode"] == "mock"

    def test_confidence_in_range(self):
        for _ in range(20):
            r = R._run_inference([1.0, 2.0, 3.0])
            assert 0.0 <= r["confidence"] <= 1.0

    def test_anomaly_score_in_range(self):
        for _ in range(20):
            r = R._run_inference([5.0, 5.0, 5.0])
            assert 0.0 <= r["anomaly_score"] <= 1.0

    def test_latency_is_positive(self):
        result = R._run_inference([1.0, 2.0])
        assert result["latency_ms"] > 0

    def test_handles_empty_values(self):
        result = R._run_inference([])
        assert result["mode"] == "mock"
        assert 0.0 <= result["anomaly_score"] <= 1.0

    def test_label_is_known_string(self):
        known_labels = {"idle", "running", "anomaly", "vibration"}
        for _ in range(30):
            r = R._run_inference([1.0])
            assert r["label"] in known_labels


# ---------------------------------------------------------------------------
# _run_inference — model mode (mocked ImpulseRunner)
# ---------------------------------------------------------------------------

class TestModelInference:
    def test_returns_model_mode_when_runner_loaded(self):
        mock_runner = MagicMock()
        mock_runner.classify.return_value = {
            "result": {
                "classification": {"idle": 0.1, "anomaly": 0.9},
                "anomaly": 0.87,
            }
        }
        R._runner = mock_runner

        result = R._run_inference([1.0, 2.0, 3.0])

        assert result["mode"] == "model"
        assert result["label"] == "anomaly"
        assert result["confidence"] == pytest.approx(0.9, abs=1e-4)
        assert result["anomaly_score"] == pytest.approx(0.87, abs=1e-4)

    def test_falls_back_to_mock_on_runner_exception(self):
        mock_runner = MagicMock()
        mock_runner.classify.side_effect = RuntimeError("model crashed")
        R._runner = mock_runner

        result = R._run_inference([1.0, 2.0])

        assert result["mode"] == "mock"


# ---------------------------------------------------------------------------
# _on_message — MQTT message handling
# ---------------------------------------------------------------------------

class TestOnMessage:
    def test_publishes_to_results_topic(self):
        client = MagicMock()
        msg = _make_mqtt_message({"device": "dev-01", "values": [1.0, 2.0, 3.0]})

        R._on_message(client, None, msg)

        client.publish.assert_called_once()
        topic, payload = client.publish.call_args[0]
        assert topic == R.MQTT_TOPIC_RESULTS
        data = json.loads(payload)
        assert data["device"] == "dev-01"

    def test_result_payload_has_expected_keys(self):
        client = MagicMock()
        msg = _make_mqtt_message({"device": "dev-01", "values": [1.0, 2.0]})

        R._on_message(client, None, msg)

        _, payload = client.publish.call_args[0]
        data = json.loads(payload)
        for key in ("device", "timestamp", "label", "confidence", "anomaly_score", "latency_ms", "mode"):
            assert key in data, f"Missing key in published payload: {key}"

    def test_increments_inference_count(self):
        client = MagicMock()
        assert R._state["inference_count"] == 0

        R._on_message(client, None, _make_mqtt_message({"device": "d", "values": [1.0]}))
        R._on_message(client, None, _make_mqtt_message({"device": "d", "values": [2.0]}))

        assert R._state["inference_count"] == 2

    def test_updates_last_inference_at(self):
        client = MagicMock()
        assert R._state["last_inference_at"] is None

        R._on_message(client, None, _make_mqtt_message({"device": "d", "values": [1.0]}))

        assert R._state["last_inference_at"] is not None

    def test_ignores_malformed_json(self):
        client = MagicMock()
        msg = _make_mqtt_message(None)
        msg.payload = b"not-valid-json"

        R._on_message(client, None, msg)

        client.publish.assert_not_called()
        assert R._state["inference_count"] == 0

    def test_ignores_message_without_values(self):
        client = MagicMock()
        msg = _make_mqtt_message({"device": "dev-01"})  # no "values" key

        R._on_message(client, None, msg)

        client.publish.assert_not_called()

    def test_ignores_message_with_empty_values(self):
        client = MagicMock()
        msg = _make_mqtt_message({"device": "dev-01", "values": []})

        R._on_message(client, None, msg)

        client.publish.assert_not_called()

    def test_uses_unknown_device_when_missing(self):
        client = MagicMock()
        msg = _make_mqtt_message({"values": [1.0, 2.0]})  # no "device" key

        R._on_message(client, None, msg)

        _, payload = client.publish.call_args[0]
        data = json.loads(payload)
        assert data["device"] == "unknown"


# ---------------------------------------------------------------------------
# _write_result_to_influxdb — InfluxDB write
# ---------------------------------------------------------------------------

class TestInfluxDBWrite:
    def test_skips_when_write_api_is_none(self):
        R._influx_write_api = None
        # Should not raise
        R._write_result_to_influxdb(
            {"device": "d", "label": "idle", "confidence": 0.9, "anomaly_score": 0.1, "latency_ms": 10.0, "mode": "mock"}
        )

    def test_calls_write_api(self):
        mock_api = MagicMock()
        R._influx_write_api = mock_api

        R._write_result_to_influxdb(
            {"device": "d", "label": "idle", "confidence": 0.9, "anomaly_score": 0.1, "latency_ms": 10.0, "mode": "mock"}
        )

        mock_api.write.assert_called_once()

    def test_silences_write_exceptions(self):
        mock_api = MagicMock()
        mock_api.write.side_effect = Exception("influxdb down")
        R._influx_write_api = mock_api

        # Should not raise
        R._write_result_to_influxdb(
            {"device": "d", "label": "idle", "confidence": 0.9, "anomaly_score": 0.1, "latency_ms": 10.0, "mode": "mock"}
        )


# ---------------------------------------------------------------------------
# Health HTTP endpoint
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    def test_health_returns_200(self):
        import threading

        from http.server import HTTPServer

        server = HTTPServer(("127.0.0.1", 0), R._HealthHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.handle_request)
        thread.daemon = True
        thread.start()

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health") as resp:
            assert resp.status == 200
            body = json.loads(resp.read())

        assert body["status"] == "ok"
        assert "mode" in body
        assert "inference_count" in body
        assert "mqtt_connected" in body

    def test_unknown_path_returns_404(self):
        import threading
        from http.server import HTTPServer

        server = HTTPServer(("127.0.0.1", 0), R._HealthHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.handle_request)
        thread.daemon = True
        thread.start()

        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/notfound")
            assert False, "expected HTTPError"
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
