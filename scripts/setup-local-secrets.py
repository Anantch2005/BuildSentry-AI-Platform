#!/usr/bin/env python3
"""Create the ignored Alertmanager bearer-token file from the local .env file."""

from pathlib import Path
import os
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"
SECRET_DIR = ROOT / ".secrets"
SECRET_FILE = SECRET_DIR / "alertmanager_secret"


def read_env_value(path: Path, key: str) -> str | None:
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() != key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        return value
    return None


def main() -> int:
    if not ENV_FILE.is_file():
        print("ERROR: .env not found. Copy .env.example to .env and configure it first.", file=sys.stderr)
        return 1

    secret = read_env_value(ENV_FILE, "ALERTMANAGER_SECRET")
    if not secret or secret.lower().startswith("replace-with-"):
        print("ERROR: Set ALERTMANAGER_SECRET to a unique, non-placeholder value in .env.", file=sys.stderr)
        return 1
    if not re.fullmatch(r"[A-Za-z0-9._~-]{32,}", secret):
        print("ERROR: Use at least 32 URL-safe characters (for example, `openssl rand -hex 24`).", file=sys.stderr)
        return 1

    SECRET_DIR.mkdir(mode=0o700, exist_ok=True)
    SECRET_FILE.write_text(secret + "\n", encoding="utf-8")
    os.chmod(SECRET_DIR, 0o700)
    os.chmod(SECRET_FILE, 0o600)
    print("Created .secrets/alertmanager_secret with restrictive permissions. The secret value was not printed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())