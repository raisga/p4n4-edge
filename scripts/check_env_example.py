#!/usr/bin/env python3
"""
Check that all environment variables referenced in docker-compose.yml
are documented in .env.example.
"""

import re
import sys
from pathlib import Path

COMPOSE_FILE = Path("docker-compose.yml")
ENV_EXAMPLE_FILE = Path(".env.example")

# Variables provided by Docker Compose itself or hardcoded in the compose file
COMPOSE_BUILTINS: set[str] = set()


def extract_compose_vars(content: str) -> set[str]:
    """Extract all ${VAR} and ${VAR:-default} references from compose YAML."""
    return set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)(?::-[^}]*)?\}", content))


def extract_env_example_vars(content: str) -> set[str]:
    """Extract all variable names defined in .env.example."""
    vars_: set[str] = set()
    for line in content.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            name = line.split("=", 1)[0].strip()
            if name:
                vars_.add(name)
    return vars_


def main() -> int:
    if not COMPOSE_FILE.exists():
        print(f"ERROR: {COMPOSE_FILE} not found", file=sys.stderr)
        return 1

    if not ENV_EXAMPLE_FILE.exists():
        print(f"ERROR: {ENV_EXAMPLE_FILE} not found", file=sys.stderr)
        return 1

    compose_vars = extract_compose_vars(COMPOSE_FILE.read_text())
    env_vars = extract_env_example_vars(ENV_EXAMPLE_FILE.read_text())

    required = compose_vars - COMPOSE_BUILTINS
    missing = required - env_vars

    if missing:
        print("FAIL: Variables referenced in docker-compose.yml but missing from .env.example:")
        for var in sorted(missing):
            print(f"  - {var}")
        return 1

    print(f"OK: All {len(required)} variable(s) from docker-compose.yml are in .env.example")
    return 0


if __name__ == "__main__":
    sys.exit(main())
