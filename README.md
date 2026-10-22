# BuildSentry — Guarded CI/CD Recovery for Jenkins

<p align="center">
  <strong>Rules classify. AI diagnoses. Policy controls. Recovery executes. Verification decides.</strong>
</p>

<p align="center">
  An AI-assisted incident-response platform that helps Jenkins pipelines diagnose failures, perform policy-approved recovery, and verify outcomes.
</p>

<p align="center">
  <a href="https://github.com/Anantch2005/BuildSentry-AI-Platform">Repository</a> ·
  <a href="https://github.com/Anantch2005/BuildSentry-AI-Platform/issues">Issues</a>
</p>

---

## Overview

CI/CD failures are not always straightforward. A single dependency error can cause several downstream stages to be skipped. A transient network failure may succeed on retry, while a code defect requires investigation rather than another build. Engineers must interpret logs, decide whether recovery is safe, execute the appropriate action, and verify the result.

**BuildSentry is designed to bring these steps into one guarded incident-response workflow for Jenkins.**

When a pipeline fails, BuildSentry collects build metadata and console logs, classifies the failure using deterministic rules, and optionally uses a locally hosted Ollama model to explain the available evidence. An independent policy engine determines whether a supported recovery action is permitted. The Jenkins Shared Library executes the approved action at the appropriate pipeline stage, and BuildSentry verifies the retry outcome before recording the result.

The incident lifecycle is stored in PostgreSQL and exposed through an observability stack built around OpenTelemetry, Prometheus, Grafana, Tempo, and Alertmanager.

BuildSentry does not give an LLM unrestricted control over Jenkins. **AI provides diagnostic assistance; deterministic rules and policy remain responsible for recovery decisions.**

> **Project status:** Active engineering project. The implementation includes automated tests and a local Docker Compose environment. Production deployment requires controlled integration testing, security review, and validation of every supported recovery path.

## Why BuildSentry?

Traditional CI/CD pipelines can tell you that a build failed, but responding to the failure often requires manual investigation and repeated trial and error.

BuildSentry addresses several parts of that workflow:

- **Failure visibility:** Collects Jenkins build metadata and console output so the incident can be investigated centrally.
- **Consistent classification:** Uses explicit rules to classify supported failure categories rather than relying on an LLM to make the operational decision.
- **Evidence-based diagnosis:** Extracts a bounded, stage-focused log excerpt and uses local AI to help explain the observed failure.
- **Controlled recovery:** Evaluates the failure against a separate policy and allowlist before requesting a supported remediation.
- **Verified retry outcomes:** Checks the recovery marker and final Jenkins build result rather than treating a successful API request as proof of recovery.
- **Incident history:** Stores incident, remediation, and audit information for later investigation.
- **Operational observability:** Provides metrics, dashboards, traces, and alerting to help engineers understand how the system behaves.

The goal is to reduce avoidable manual work and make recovery more consistent, explainable, and auditable—not to promise automatic repair of every possible pipeline failure.

## Architecture

```text
                    JENKINS CI/CD
                          |
                    Pipeline failure
                          |
                          v
               Authenticated webhook
                          |
                          v
               BUILDSENTRY BACKEND
                    (FastAPI)
                          |
                  Jenkins Collector
                          |
              Build metadata + console log
                          |
             +------------+-------------+
             |                          |
             v                          v
     Deterministic Rules          AI Evidence Pipeline
     Failure Classification      Focused Log Extraction
             |                    Redaction + Validation
             |                          |
             |                          v
             |                    Local Ollama
             |                    Advisory Diagnosis
             |                          |
             +-------------+------------+
                           |
                           v
                     Policy Engine
                  Allowlist + Safety Rules
                           |
                 +---------+---------+
                 |                   |
                 v                   v
          Approved Recovery      Escalation
                 |
                 v
         Remediation Executor
                 |
                 v
       Jenkins Shared Library
          Recovery Action
                 |
                 v
          Retry Build
                 |
                 v
       Marker + Result Verification
                 |
                 v
          PostgreSQL Audit
                 |
                 v
      OpenTelemetry / Prometheus
        Grafana / Tempo / Alerts
```

### End-to-end workflow

1. **Detect:** Jenkins reports a failed build to the authenticated BuildSentry webhook.
2. **Collect:** The backend fetches build metadata and console output through the Jenkins REST API.
3. **Classify:** Deterministic rules select the failure category and proposed action.
4. **Diagnose:** The AI pipeline prepares focused evidence and optionally asks local Ollama for an advisory explanation.
5. **Evaluate:** The policy engine determines whether the proposed recovery is allowed and applies the configured attempt limits.
6. **Recover or escalate:** An approved action is passed to the Jenkins Shared Library. Unsupported or unsafe failures are escalated instead of receiving unrestricted automated repair.
7. **Verify:** BuildSentry checks the recovery action marker and retry result.
8. **Record and observe:** Incident data and outcomes are persisted, while telemetry supports dashboards and alerts.

## Core engineering principles

### 1. Deterministic classification

BuildSentry uses explicit Python rules to recognize supported failure signatures. The classifier proposes a category and associated action; the policy engine separately determines whether that action is permitted.

This separation makes operational behavior easier to inspect, test, and constrain. It also avoids making an LLM's free-form response the authority for automated recovery.

### 2. Local AI for privacy-conscious diagnosis

BuildSentry integrates Ollama so diagnostic inference can run locally instead of requiring a hosted LLM API.

This is an intentional architecture choice for a system that processes CI/CD logs, which may contain internal paths, package information, infrastructure details, or accidentally exposed credentials.

The AI context pipeline is designed to:

- Select a focused excerpt instead of sending an unnecessarily large console log.
- Apply best-effort secret redaction.
- Ask the model for a diagnosis supported by the available evidence.
- Validate cited quotations against the supplied excerpt.
- Preserve an unverified result when the evidence cannot be validated.

Local inference can reduce external data exposure, but it does not guarantee complete privacy. Jenkins, the BuildSentry backend, logs, storage, and model runtime must still be secured.

### 3. AI cannot authorize remediation

The model is advisory only. It does not independently select arbitrary shell commands, bypass policy, or authorize privileged recovery.

The deterministic classifier and policy engine control the operational path. This reduces the risk of a plausible but incorrect AI diagnosis directly triggering an unsafe action.

### 4. Guarded, bounded recovery

Supported recovery operations are explicitly implemented. Retry limits and safety checks help constrain automated behavior.

BuildSentry is not designed to silently rewrite application code, modify dependency versions, fix invalid registry credentials, or perform unrestricted infrastructure administration.

When a failure is unsupported or unsafe to repair automatically, escalation is the intended behavior.

### 5. Verify the outcome, not just the action

A remediation request can succeed while the pipeline remains broken. A retry can also pass without the expected recovery action having executed.

BuildSentry therefore uses a recovery marker and the retry build result as verification signals. If the required marker is absent or the retry fails, the system should not classify the recovery as verified merely because the action was requested.

These signals provide operational verification; they are not cryptographic proof that every side effect in arbitrary pipeline code occurred.

### 6. Observability and auditability

Incident response should itself be observable.

BuildSentry combines PostgreSQL persistence with metrics, traces, dashboards, and alerting so engineers can investigate incident categories, remediation attempts, processing time, and recorded outcomes.

---

## Supported failure handling

The repository contains deterministic rules and policy entries for the following failure categories. Detection and recovery depend on the actual error signatures and correct Jenkins integration.

| Failure category | Intended handling | Safety boundary |
|---|---|---|
| `FLAKY_TEST` | One controlled retry | Does not change application or test code. |
| `NETWORK_FAILURE` | Connectivity check, backoff, and retry | Does not repair persistent network infrastructure problems. |
| `WORKSPACE_FAILURE` | Scoped workspace cleanup and fresh checkout | Requires the correct checkout-stage hook and agent permissions. |
| `DEPENDENCY_FAILURE` | Rebuild the supported dependency environment and retry | Uses existing manifests; does not intentionally change dependency versions. |
| `DOCKER_FAILURE` | Scoped Docker cache-bypass recovery | The Jenkinsfile must actually consume the no-cache flag. |
| `REGISTRY_FAILURE` | One controlled retry for potentially transient errors | Does not fix invalid credentials or missing permissions. |
| `CODE_FAILURE` | Do not automatically heal; investigate | Application and test code require human review. |
| `REGISTRY_AUTH_FAILURE` | Escalate | Credential and permission problems require review. |
| `DOCKER_DAEMON_FAILURE` | Escalate | Cache bypass cannot repair an unavailable daemon. |
| `DISK_FULL` | Escalate | Privileged agent-wide cleanup is not implemented. |
| `OOM` | Escalate | Resource resizing or agent replacement is not implemented. |
| `AGENT_FAILURE` | Escalate | Privileged agent recovery is not implemented. |
| `UNKNOWN` | Escalate | Requires investigation rather than speculative remediation. |

The presence of a rule does not guarantee that every variation of a failure will be recognized. Test real console output from the target pipeline before relying on automated handling.

## Technology stack

| Area | Technologies |
|---|---|
| Backend API | Python 3.12, FastAPI, Pydantic Settings |
| CI/CD integration | Jenkins REST API, Jenkins Shared Library (Groovy) |
| Classification | Deterministic Python rules |
| AI diagnosis | Ollama, locally hosted language model |
| Persistence | PostgreSQL, SQLAlchemy |
| Runtime | Docker Engine, Docker Compose v2 |
| Metrics and alerts | Prometheus, Alertmanager |
| Tracing and telemetry | OpenTelemetry, Tempo |
| Dashboards | Grafana |
| Testing | pytest, pytest-asyncio, mocked external services |

## Repository structure

```text
.
├── app/
│   ├── ai/                 # AI client, prompts, evidence selection and validation
│   ├── api/                # Jenkins webhook and Alertmanager endpoint
│   ├── classifier/         # Deterministic failure rules
│   ├── collectors/         # Jenkins build metadata and console logs
│   ├── database/           # SQLAlchemy models, connection and repository
│   ├── observability/      # Metrics and tracing instrumentation
│   ├── policy/             # Policy decisions and recovery allowlist
│   ├── remediation/        # Recovery plans and Jenkins retry coordination
│   ├── safety/             # Circuit breaker
│   ├── services/           # Incident orchestration and reporting
│   ├── config.py           # Environment-based configuration
│   ├── main.py             # FastAPI application and health endpoint
│   └── models.py           # Shared domain and request models
├── jenkins-shared-library/
│   └── vars/
│       └── buildsentry.groovy
├── jenkins/
│   └── BuildSentryRemediation.example.groovy
├── observability/
│   ├── alertmanager/
│   ├── grafana/
│   ├── prometheus/
│   ├── otel-collector-config.yaml
│   └── tempo.yaml
├── scripts/
│   └── setup-local-secrets.py
├── tests/
├── Dockerfile
├── docker-compose.yml
├── docker-compose.gpu.yml
├── requirements.txt
└── .env.example
```

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
