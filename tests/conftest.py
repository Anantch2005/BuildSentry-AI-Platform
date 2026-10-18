"""Deterministic offline test configuration."""

import os
from pathlib import Path

# Set these explicitly rather than using setdefault: an interactive shell may
# have production environment variables that should never affect unit tests.
TEST_ENV = {
    "JENKINS_URL": "http://127.0.0.1:8080",
    "JENKINS_USERNAME": "test-user",
    "JENKINS_API_TOKEN": "test-token",
    "WEBHOOK_SECRET": "change-me",
    "ALERTMANAGER_SECRET": "change-me-alertmanager",
    "DATABASE_URL": "sqlite:////tmp/autoheal-test.db",
    "AI_ENABLED": "false",
    "OLLAMA_URL": "http://127.0.0.1:11434",
    "OLLAMA_MODEL": "llama3.2:3b",
    "AI_MAX_LOG_CHARS": "12000",
    "OTEL_ENDPOINT": "http://127.0.0.1:4318",
    "OTEL_ENABLED": "false",
}
os.environ.update(TEST_ENV)

# Isolate the test database so repeated local test runs are deterministic.
TEST_DB = Path("/tmp/autoheal-test.db")
TEST_DB.unlink(missing_ok=True)