"""
Tests for scripts/check_env_example.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import check_env_example as C  # noqa: E402


_REPO_ROOT = Path(__file__).parent.parent


class TestExtractComposeVars:
    def test_simple_var(self):
        assert C.extract_compose_vars("${FOO}") == {"FOO"}

    def test_var_with_default(self):
        assert C.extract_compose_vars("${BAR:-default}") == {"BAR"}

    def test_multiple_vars(self):
        result = C.extract_compose_vars("${FOO:-x} ${BAR} ${BAZ:-y}")
        assert result == {"FOO", "BAR", "BAZ"}

    def test_no_vars(self):
        assert C.extract_compose_vars("image: nginx:latest") == set()

    def test_ignores_lowercase(self):
        # Lowercase env vars are not a pattern we use
        assert C.extract_compose_vars("${lowercase}") == set()


class TestExtractEnvExampleVars:
    def test_simple_key(self):
        assert C.extract_env_example_vars("FOO=bar\n") == {"FOO"}

    def test_ignores_comments(self):
        assert C.extract_env_example_vars("# comment\nFOO=bar\n") == {"FOO"}

    def test_ignores_blank_lines(self):
        assert C.extract_env_example_vars("\n\nFOO=bar\n\n") == {"FOO"}

    def test_multiple_keys(self):
        content = "FOO=1\nBAR=2\nBAZ=3\n"
        assert C.extract_env_example_vars(content) == {"FOO", "BAR", "BAZ"}

    def test_empty_value_is_included(self):
        assert C.extract_env_example_vars("FOO=\n") == {"FOO"}


class TestMainAgainstActualFiles:
    def test_actual_env_example_covers_all_compose_vars(self, tmp_path, monkeypatch):
        """The real .env.example must cover every var referenced in docker-compose.yml."""
        monkeypatch.chdir(_REPO_ROOT)
        result = C.main()
        assert result == 0

    def test_missing_var_is_detected(self, tmp_path, monkeypatch):
        """Removing a var from .env.example causes main() to return non-zero."""
        compose = tmp_path / "docker-compose.yml"
        compose.write_text("environment:\n  FOO: ${MISSING_VAR:-default}\n")
        env_example = tmp_path / ".env.example"
        env_example.write_text("OTHER_VAR=something\n")
        monkeypatch.chdir(tmp_path)

        result = C.main()
        assert result != 0
