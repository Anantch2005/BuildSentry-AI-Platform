# BuildSentry — Guarded CI/CD Recovery and AI Diagnostics

BuildSentry is a Jenkins incident-response platform that classifies failed builds, applies deterministic safety policies, optionally uses a local Ollama model to explain log evidence, and records the incident lifecycle for auditing and observability.

## Features

- Authenticated Jenkins failure notifications.
- Jenkins build metadata and console-log collection.
- Deterministic failure classification.
- Bounded, redacted, stage-focused evidence sent to local Ollama.
- Validation that AI evidence quotations occur in the supplied excerpt.
- Confidence limits when the AI cannot establish a clear root cause.
- Separate policy evaluation, guarded remediation, and recovery verification.
- PostgreSQL incident history and audit records.
- Prometheus metrics, Grafana dashboards, OpenTelemetry traces, Tempo, and authenticated Alertmanager notifications.

## Architecture

```text
Jenkins failed build
        |
        v
Authenticated BuildSentry webhook
        |
        v
Collect build metadata and console logs
        |
        +-----------------------+
        |                       |
        v                       v
Deterministic rules       Focused log extractor
        |                       |
        v                       v
Policy Engine             Local Ollama diagnosis
        |                       |
        +------------+----------+
                     |
                     v
      Guarded remediation or escalation
                     |
                     v
       Verification and PostgreSQL audit
                     |
                     v
    Prometheus / Grafana / Tempo / alerts
```

## Technology stack

- Python 3.12, FastAPI, Pydantic Settings
- Jenkins REST API and Jenkins Shared Library (Groovy)
- PostgreSQL and SQLAlchemy
- Ollama for optional local AI diagnostics
- Docker Engine and Docker Compose
- OpenTelemetry Collector, Prometheus, Grafana, Tempo, Alertmanager
- pytest and pytest-asyncio

## Requirements

- Docker Engine and Docker Compose v2.
- Python 3.12 for local tests.
- Git to clone the repository.
- Sufficient disk space for Docker images, database storage, and the Ollama model. The `llama3.2:3b` model requires additional storage beyond the Ollama Docker image.
- A Jenkins instance reachable from the BuildSentry container.
- A Jenkins API user/token with permission to read build metadata and console output, and to trigger builds when recovery requires it.
- A Jenkins Secret Text credential named `autoheal-webhook-secret`, matching `WEBHOOK_SECRET` in `.env`.
- A Jenkins global Shared Library named `BuildSentry`, pointing to this repository's `jenkins-shared-library` directory.

## Quick start

### 1. Clone the repository

Clone the repository and enter the project directory:

```bash
git clone https://github.com/Anantch2005/BuildSentry-AI-Platform.git
```

### 2. Configure environment variables

Create your local environment file:

```bash
cp .env.example .env
```

Edit `.env` before starting the stack.

At a minimum, review the following settings and ensure they match your intended configuration:

```dotenv
AI_ENABLED=true
OLLAMA_URL=http://ollama:11434
OLLAMA_MODEL=llama3.2:3b
```

Also configure the database credentials, webhook secret, and Grafana administrator password. Keep `DATABASE_URL` consistent with `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB`.

Use a strong, unique `GRAFANA_ADMIN_PASSWORD`. URL-encode special characters in database passwords if they appear in `DATABASE_URL`.

Generate a webhook secret:

```bash
openssl rand -hex 32
```

Put the generated value in `.env` as `WEBHOOK_SECRET`. Configure the same value in the Jenkins Secret Text credential named `autoheal-webhook-secret`.

Create the local Alertmanager secret file:

```bash
python scripts/setup-local-secrets.py
```

This script creates `.secrets/alertmanager_secret` with restrictive permissions. The file should not be committed to Git.

**Security:** Never commit `.env`, `.secrets/`, API tokens, database passwords, or webhook URLs.

### 3. Validate and start Docker Compose

Validate the Compose configuration:

```bash
docker compose config
```

Start the application and supporting services:

```bash
docker compose up --build -d
```

Check the services:

```bash
docker compose ps
```

The Compose project uses the BuildSentry naming convention, with containers such as `buildsentry`, `buildsentry-postgres`, `buildsentry-ollama`, and `buildsentry-grafana`.

Wait for services to initialize. Some services may take longer than others to become healthy.

### 4. Install the Ollama model

**Important: Starting the Ollama container does not automatically download the AI model.** The Ollama Docker image contains the Ollama runtime, while model weights are stored separately in the Ollama data volume.

After the Compose stack is running, download the configured model:

```bash
docker compose exec ollama ollama pull llama3.2:3b
```

This step requires an internet connection and can take several minutes. The model is downloaded into Ollama's persistent storage.

Verify that the model is installed:

```bash
docker compose exec ollama ollama list
```

You should see `llama3.2:3b` in the output.

Test the model:

```bash
docker compose exec ollama ollama run llama3.2:3b \
  "Reply with: BuildSentry AI is working"
```

If you use a different model, replace `llama3.2:3b` in the commands and ensure `OLLAMA_MODEL` in `.env` matches the installed model name.

Restart the application after changing its environment configuration:

```bash
docker compose up -d --force-recreate buildsentry
```

Verify the configuration loaded by the application:

```bash
docker compose exec buildsentry printenv AI_ENABLED OLLAMA_URL OLLAMA_MODEL
```

Expected values for the default setup:

```text
true
http://ollama:11434
llama3.2:3b
```

If AI is disabled or Ollama is unavailable, BuildSentry's deterministic classification and safety policy should remain authoritative. AI diagnostics are advisory and must not authorize remediation.

**First-run checklist:**

- [ ] Docker Compose services have started.
- [ ] The BuildSentry API is healthy.
- [ ] PostgreSQL is healthy.
- [ ] Ollama is running.
- [ ] `llama3.2:3b` appears in `ollama list`.
- [ ] `AI_ENABLED=true` is configured.
- [ ] The application can reach Ollama.

> **Note for contributors:** With the current setup, the model pull is a separate first-run step. If automatic model installation is added to Docker Compose in the future, update this section to reflect that behavior.

### 5. Open the services

The Compose configuration binds application ports to localhost by default. The following endpoints are available when their corresponding ports are published by Compose:

| Service | Local URL |
|---|---|
| BuildSentry API health | http://localhost:8000/health |
| Grafana | http://localhost:3000 |
| Prometheus | http://localhost:9090 |
| Alertmanager | http://localhost:9093 |
| Ollama API | http://localhost:11434 |

Use the Grafana credentials configured in `.env`. Do not expose these services publicly without appropriate authentication, TLS, and network controls.

### 6. Check service status and logs

List all containers:

```bash
docker ps
```

Check Compose service status:

```bash
docker compose ps
```

Inspect logs from all Compose services:

```bash
docker compose logs --tail=100
```

Inspect specific services when troubleshooting:

```bash
docker compose logs --tail=100 buildsentry
docker compose logs --tail=100 ollama
```

Use the service names reported by:

```bash
docker compose config --services
```

If a service is restarting or unhealthy, inspect its logs before assuming the entire stack is working.

### 7. Optional NVIDIA GPU support

CPU inference is the default. If your machine has a supported NVIDIA GPU and the NVIDIA Container Toolkit is installed and configured, you can try the GPU override:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.gpu.yml \
  up --build -d
```

The GPU override requires a working NVIDIA Container Toolkit/runtime. GPU support is optional; the model can also run on CPU, although inference may be slower.

## Jenkins integration

The Shared Library entrypoint is:

`jenkins-shared-library/vars/buildsentry.groovy`

Configure a global Jenkins Shared Library named `BuildSentry` that points to this repository. Adapt `jenkins/BuildSentryRemediation.example.groovy` to your pipeline.

Example stage hooks:

```groovy
buildsentry(stage: 'checkout') // before checkout scm
buildsentry(stage: 'test')     // before dependency setup/tests
buildsentry(stage: 'docker')   // before Docker build
buildsentry(stage: 'registry') // before registry push
```

Place each hook at the stage where its corresponding recovery action can safely execute.

A stage-bound action called from the wrong stage is not executed. BuildSentry escalates if the expected action marker is missing, even if a later retry passes.

For Docker cache recovery, the pipeline must consume `BUILDSENTRY_DOCKER_NO_CACHE` and actually invoke `docker build --no-cache`. Registry operations still require properly configured Jenkins credentials. Never hard-code credentials in a Jenkinsfile.

The pipeline's `post { failure { ... } }` block should call `buildsentry()` to notify the backend.

Configure the Jenkins Secret Text credential `autoheal-webhook-secret` to match `WEBHOOK_SECRET` in `.env`. The backend accepts the `X-BuildSentry-Secret` header and retains compatibility with the legacy `X-AutoHeal-Secret` header.

### Validate recovery safely

Use a disposable Jenkins job/agent and a non-production registry. Preserve the original failed build, recovery build, incident/audit trail, and final result.

1. **`DEPENDENCY_FAILURE`:** Introduce a controlled missing test dependency and verify the hook runs and the retry succeeds.
2. **`WORKSPACE_FAILURE`:** Inject a workspace-only fault and verify cleanup occurs at the expected stage.
3. **`NETWORK_FAILURE`:** Inject a controlled transient connectivity failure and verify bounded checks and backoff.
4. **`DOCKER_FAILURE`:** Verify the retry actually consumes `BUILDSENTRY_DOCKER_NO_CACHE`.
5. **`CODE_FAILURE` and `UNKNOWN`:** Verify unsupported failures are escalated rather than automatically repaired.
6. **Missing action hook:** Verify a passing retry without the required action marker is not reported as healed.
7. **Bad webhook secret:** Verify HTTP 401 and no incident creation.

An HTTP 202 means the failure notification was accepted for asynchronous processing. It does not, by itself, mean the incident was healed.

## Run tests locally

Create a virtual environment:

```bash
python -m venv .venv
```

Activate it on Linux/macOS:

```bash
source .venv/bin/activate
```

On Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Install dependencies and run the tests:

```bash
python -m pip install -r requirements.txt
python -m compileall -q app tests
pytest -q
```

Unit tests use an isolated SQLite database and mock external Jenkins/Ollama calls. They do not replace live Jenkins, Groovy, Docker, Prometheus, or Alertmanager integration testing.

## Observability

- OpenTelemetry exports metrics and traces when `OTEL_ENABLED=true`.
- Prometheus scrapes the Collector metrics endpoint at `otel-collector:8889` and loads rules from `observability/prometheus/rules/`.
- Grafana is provisioned with Prometheus, Tempo, and PostgreSQL data sources and dashboard JSON under `observability/grafana/dashboards/`.
- Alertmanager calls `http://buildsentry:8000/alerts/alertmanager` with the bearer secret mounted at `/run/secrets/alertmanager_secret`.
- `SLACK_WEBHOOK_URL` is optional. Verify alert delivery before relying on notifications.

In Prometheus, open **Status → Targets** and verify the Collector target is up. Check **Status → Rules** for loaded alert rules.

A successful `docker compose config` only validates the Compose configuration. It does not prove that every service is healthy or that AI diagnostics are working.

## Data persistence and cleanup

BuildSentry uses Docker volumes to persist service data, including PostgreSQL records, Grafana data, and Ollama model files.

List volumes:

```bash
docker volume ls
```

Inspect the volumes associated with the current Compose project:

```bash
docker compose config
docker compose ps
```

Do not assume that volumes from an older project name are automatically reused. When changing Compose project or volume names, Docker may create new volumes, leaving older data in separate volumes.

**Normal stop and restart:**

```bash
docker compose stop
docker compose start
```

**Stop and remove containers without deleting named volumes:**

```bash
docker compose down
```

**Warning:** Avoid `docker compose down -v` unless you intentionally want to remove the project's named volumes. Removing volumes can permanently delete database records, Grafana state, and downloaded Ollama models.

If you intentionally want a fresh Ollama model store, back up anything you need first, then remove only the verified Ollama volume and recreate the Ollama service. Do not remove the PostgreSQL or Grafana volumes just to reinstall a model.

## Security

- Never commit `.env`, `.secrets/`, Jenkins API tokens, database passwords, or Slack webhook URLs.
- Application and observability ports should remain bound to localhost by default unless intentionally configured otherwise.
- Do not expose internal services publicly without authentication, TLS, and network controls.
- Rotate credentials that have been committed or exposed in logs/screenshots.
- Jenkins agents with Docker socket access effectively have privileged access to the Docker host. Use disposable agents for recovery tests.
- Log redaction is best-effort; avoid printing secrets in Jenkins logs.
- Review Trivy findings. Do not suppress security scans just to obtain a green pipeline.

## Known limitations

- FastAPI background tasks run in-process and are not a durable queue. A process crash after HTTP 202 can interrupt processing.
- The circuit breaker is in memory and resets when the API process restarts. It is not a distributed lock.
- The action marker verifies that the Shared Library reached its marker and the retry passed. It is not cryptographic proof of every side effect inside arbitrary pipeline code.
- The Shared Library requires correctly placed stage hooks and appropriate Jenkins agent tools/permissions.
- AI evidence matching verifies that cited text occurs in the supplied excerpt; it cannot prove that the model's interpretation is correct. AI remains advisory and cannot authorize remediation.
- A running Ollama container does not guarantee that the required model is installed or that inference will succeed.
- Passing unit tests does not establish production readiness. Validate every recovery path in a disposable environment.

## License

MIT. See [`LICENSE`](LICENSE).
