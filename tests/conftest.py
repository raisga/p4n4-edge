"""
Stub heavy optional dependencies (edge_impulse_linux, onnxruntime, numpy,
influxdb_client) before any test module imports runner.py.
"""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# edge_impulse_linux stub
# ---------------------------------------------------------------------------
_ei_stub = ModuleType("edge_impulse_linux")
_ei_runner_stub = ModuleType("edge_impulse_linux.runner")
_ei_runner_stub.ImpulseRunner = MagicMock()
_ei_stub.runner = _ei_runner_stub
sys.modules.setdefault("edge_impulse_linux", _ei_stub)
sys.modules.setdefault("edge_impulse_linux.runner", _ei_runner_stub)

# ---------------------------------------------------------------------------
# onnxruntime + numpy stubs
# ---------------------------------------------------------------------------
_ort_stub = ModuleType("onnxruntime")
_ort_stub.InferenceSession = MagicMock()
sys.modules.setdefault("onnxruntime", _ort_stub)

_np_stub = ModuleType("numpy")
_np_stub.asarray = MagicMock(return_value=MagicMock())
_np_stub.float32 = MagicMock()
# pytest.approx introspects sys.modules["numpy"] — keep these functional
_np_stub.isscalar = lambda obj: False
_np_stub.ndarray = type("ndarray", (), {})
_np_stub.bool_ = type("bool_", (), {})
sys.modules.setdefault("numpy", _np_stub)

# ---------------------------------------------------------------------------
# influxdb_client stub
# ---------------------------------------------------------------------------
_influx_stub = ModuleType("influxdb_client")
_influx_stub.InfluxDBClient = MagicMock()
_influx_stub.Point = MagicMock(return_value=MagicMock())
_influx_stub.WritePrecision = MagicMock()
_influx_client_stub = ModuleType("influxdb_client.client")
_influx_write_stub = ModuleType("influxdb_client.client.write_api")
_influx_write_stub.SYNCHRONOUS = MagicMock()
sys.modules.setdefault("influxdb_client", _influx_stub)
sys.modules.setdefault("influxdb_client.client", _influx_client_stub)
sys.modules.setdefault("influxdb_client.client.write_api", _influx_write_stub)
