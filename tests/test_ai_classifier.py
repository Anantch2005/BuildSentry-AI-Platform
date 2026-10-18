import json

import pytest

from app.ai.classifier import AIClassifier


class FakeResponse:

    def __init__(
        self,
        payload,
    ):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeAsyncClient:

    def __init__(
        self,
        response,
    ):
        self.response = response
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(
        self,
        exc_type,
        exc,
        tb,
    ):
        return False

    async def post(
        self,
        url,
        **kwargs,
    ):
        self.calls.append(
            (
                url,
                kwargs,
            )
        )

        return self.response


# =============================================================
# DISABLED AI
# =============================================================


@pytest.mark.asyncio
async def test_ai_disabled_raises():

    classifier = AIClassifier()

    classifier.enabled = False

    with pytest.raises(
        RuntimeError,
        match="AI analysis is disabled",
    ):

        await classifier.classify(
            "Some Jenkins failure"
        )


# =============================================================
# SECRET REDACTION
# =============================================================


def test_ai_log_redaction():

    classifier = AIClassifier()

    prepared = classifier._redact_secrets(
        """
        Authorization: Bearer super-secret
        api_key=top-secret
        password=hidden
        token=abc123
        secret=my-secret
        access_key=access-secret
        """
    )

    assert (
        "super-secret"
        not in prepared
    )

    assert (
        "top-secret"
        not in prepared
    )

    assert (
        "hidden"
        not in prepared
    )

    assert (
        "abc123"
        not in prepared
    )

    assert (
        "my-secret"
        not in prepared
    )

    assert (
        "access-secret"
        not in prepared
    )

    assert (
        prepared.count(
            "[REDACTED]"
        )
        >= 6
    )


# =============================================================
# STAGE DETECTION
# =============================================================


def test_ai_extracts_failed_stage():

    classifier = AIClassifier()

    log = """
[Pipeline] Start of Pipeline
[Pipeline] { (Checkout)
[Pipeline] sh
Checking out repository
[Pipeline] }
[Pipeline] { (Test)
[Pipeline] sh
pytest -q
============================= test session starts =============================
FAILED tests/test_calculator.py::test_add
E       assert -1 == 5
AssertionError
script returned exit code 1
[Pipeline] }
[Pipeline] { (Docker Build)
docker build .
[Pipeline] }
[Pipeline] End of Pipeline
"""

    prepared = classifier._prepare_log(
        log,
        rules_category="CODE_FAILURE",
    )

    assert (
        "Failed Jenkins stage: Test"
        in prepared
    )

    assert (
        "FAILED tests/test_calculator.py::test_add"
        in prepared
    )

    assert (
        "AssertionError"
        in prepared
    )

    # The unrelated Docker stage should not become
    # the main AI evidence.
    assert (
        "docker build ." not in prepared
    )


# =============================================================
# FAILURE WINDOW WITHOUT STAGE
# =============================================================


def test_ai_uses_failure_context_without_stage():

    classifier = AIClassifier()

    log = "\n".join(
        [
            "normal line 1",
            "normal line 2",
            "normal line 3",
            "ERROR: connection refused",
            "normal line after failure",
        ]
    )

    prepared = classifier._prepare_log(
        log,
        rules_category="NETWORK_FAILURE",
    )

    assert (
        "connection refused"
        in prepared
    )

    assert (
        "normal line after failure"
        in prepared
    )


# =============================================================
# SMALL FAILURE CONTEXT
# =============================================================


def test_ai_context_is_bounded():

    classifier = AIClassifier()

    classifier.max_log_chars = 3000
    classifier.max_stage_chars = 1500

    lines = [
        "[Pipeline] { (Huge Stage)"
    ]

    for index in range(1000):

        lines.append(
            f"normal pipeline output {index}"
        )

    lines.extend(
        [
            "ERROR: actual failure happened",
            "script returned exit code 1",
            "[Pipeline] }",
        ]
    )

    prepared = classifier._prepare_log(
        "\n".join(lines),
        rules_category="UNKNOWN",
    )

    assert len(prepared) <= 3300

    assert (
        "actual failure happened"
        in prepared
    )


# =============================================================
# REAL OLLAMA RESPONSE
# =============================================================


@pytest.mark.asyncio
async def test_ai_classification_parses_real_ollama_response(
    monkeypatch,
):

    classifier = AIClassifier()

    classifier.enabled = True

    payload = {
        "response": json.dumps(
            {
                "category": (
                    "pytest_assertion_failure"
                ),
                "root_cause": (
                    "The add() implementation "
                    "returned the wrong value."
                ),
                "reasoning": (
                    "The targeted Test stage contains "
                    "a pytest assertion failure."
                ),
                "confidence": 0.94,
                "matched_evidence": [
                    (
                        "FAILED "
                        "tests/test_calculator.py::test_add"
                    ),
                    "assert -1 == 5",
                    "AssertionError",
                ],
                "recommendations": [
                    (
                        "Check the implementation "
                        "of add(a, b)."
                    ),
                    (
                        "Run the failing pytest "
                        "test after correction."
                    ),
                ],
            }
        )
    }

    fake_client = FakeAsyncClient(
        FakeResponse(payload)
    )

    def make_client(
        *args,
        **kwargs,
    ):
        return fake_client

    monkeypatch.setattr(
        "app.ai.classifier.httpx.AsyncClient",
        make_client,
    )

    log = """
[Pipeline] { (Test)
pytest -q
FAILED tests/test_calculator.py::test_add
E       assert -1 == 5
AssertionError
script returned exit code 1
[Pipeline] }
"""

    result = await classifier.classify(
        log,
        rules_category="CODE_FAILURE",
    )

    assert (
        result.category
        == "pytest_assertion_failure"
    )

    assert (
        result.confidence
        == 0.94
    )

    assert (
        "add() implementation"
        in result.root_cause
    )

    assert (
        "AssertionError"
        in result.matched_evidence
    )

    assert (
        len(result.recommendations)
        == 2
    )

    assert (
        len(fake_client.calls)
        == 1
    )

    url, kwargs = (
        fake_client.calls[0]
    )

    assert url.endswith(
        "/api/generate"
    )

    assert (
        kwargs["json"]["stream"]
        is False
    )

    assert (
        kwargs["json"]["format"]
        == "json"
    )

    # Critical production requirement:
    #
    # Ollama must NOT receive the complete Jenkins
    # console. It should receive only the targeted context.
    sent_prompt = kwargs[
        "json"
    ]["prompt"]

    assert (
        "TARGETED FAILURE EVIDENCE"
        in sent_prompt
    )

    assert (
        "COMPLETE AVAILABLE JENKINS CONSOLE"
        not in sent_prompt
    )


# =============================================================
# AI ACCEPTS FREE-FORM DIAGNOSTIC CATEGORIES
# =============================================================


@pytest.mark.asyncio
async def test_ai_accepts_free_form_diagnostic_category(
    monkeypatch,
):

    classifier = AIClassifier()

    classifier.enabled = True

    payload = {
        "response": json.dumps(
            {
                "category": (
                    "python_dependency_installation"
                ),
                "root_cause": (
                    "The requested package version "
                    "could not be installed."
                ),
                "reasoning": (
                    "The dependency stage reports "
                    "No matching distribution found."
                ),
                "confidence": 0.91,
                "matched_evidence": [
                    (
                        "No matching distribution "
                        "found"
                    )
                ],
                "recommendations": [
                    (
                        "Check whether the requested "
                        "package version exists."
                    )
                ],
            }
        )
    }

    fake_client = FakeAsyncClient(
        FakeResponse(payload)
    )

    def make_client(
        *args,
        **kwargs,
    ):
        return fake_client

    monkeypatch.setattr(
        "app.ai.classifier.httpx.AsyncClient",
        make_client,
    )

    result = await classifier.classify(
        """
[Pipeline] { (Test)
pip install -r requirements.txt
ERROR: No matching distribution found
[Pipeline] }
""",
        rules_category="DEPENDENCY_FAILURE",
    )

    assert (
        result.category
        == "python_dependency_installation"
    )

    assert (
        result.confidence
        == 0.91
    )

    assert (
        result.recommendations
    )

def test_ai_classifier_reads_enabled_setting(monkeypatch):
    monkeypatch.setattr("app.ai.classifier.settings.ai_enabled", True)
    monkeypatch.setattr("app.ai.classifier.settings.ollama_url", "http://ollama-test:11434/")
    classifier = AIClassifier()
    assert classifier.enabled is True
    assert classifier.ollama_url == "http://ollama-test:11434"



def test_ai_extracts_failed_stage_with_jenkins_timestamps():
    """Real Jenkins console output may prefix Pipeline markers with timestamps."""
    classifier = AIClassifier()
    log = """\
10:49:32  [Pipeline] { (Checkout)
10:49:33  [Pipeline] echo
10:49:34  [Pipeline] { (Test)
10:49:35  [Pipeline] sh
10:49:36  /venv/bin/python: No module named pytest
10:49:37  script returned exit code 1
10:49:38  [Pipeline] }
10:49:39  [Pipeline] { (Docker Build)
10:49:40  docker build .
10:49:41  [Pipeline] }
"""
    prepared = classifier._prepare_log(
        log,
        rules_category="DEPENDENCY_FAILURE",
    )
    assert "Failed Jenkins stage: Test" in prepared
    assert "No module named pytest" in prepared
    assert "docker build ." not in prepared