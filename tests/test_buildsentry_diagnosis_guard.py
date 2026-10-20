import json

import pytest

from app.ai.classifier import AIClassifier


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeAsyncClient:
    def __init__(self, response, *args, **kwargs):
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, *args, **kwargs):
        return self.response


@pytest.mark.asyncio
async def test_uncertain_diagnosis_cannot_keep_high_confidence(monkeypatch):
    classifier = AIClassifier()
    classifier.enabled = True

    response = FakeResponse({
        "response": json.dumps({
            "category": "unknown",
            "root_cause": "The root cause is unverified.",
            "reasoning": (
                "The error does not specify why the server query failed."
            ),
            "confidence": 0.95,
            "matched_evidence": ["Failed to query server version"],
            "recommendations": [
                "Inspect the server logs and URL configuration."
            ],
        })
    })

    monkeypatch.setattr(
        "app.ai.classifier.httpx.AsyncClient",
        lambda *args, **kwargs: FakeAsyncClient(response),
    )

    result = await classifier.classify(
        """[Pipeline] { (Analysis)
Failed to query server version
[Pipeline] }""",
        rules_category="UNKNOWN",
    )

    assert result.confidence <= 0.35
    assert result.matched_evidence == [
        "Failed to query server version"
    ]


@pytest.mark.asyncio
async def test_benign_git_notices_are_not_evidence_for_later_failure(monkeypatch):
    classifier = AIClassifier()
    classifier.enabled = True

    response = FakeResponse({
        "response": json.dumps({
            "category": "service_endpoint_failure",
            "root_cause": (
                "The endpoint check failed; its underlying cause is unverified."
            ),
            "reasoning": (
                "The error identifies the failed operation but not the reason."
            ),
            "confidence": 0.8,
            "matched_evidence": [
                "ERROR: service endpoint is not available"
            ],
            "recommendations": [
                "Inspect the endpoint and service logs."
            ],
        })
    })

    monkeypatch.setattr(
        "app.ai.classifier.httpx.AsyncClient",
        lambda *args, **kwargs: FakeAsyncClient(response),
    )

    result = await classifier.classify(
        """[Pipeline] { (Checkout)
The recommended git tool is: NONE
Selected Git installation does not exist. Using Default
No credentials specified
[Pipeline] }
[Pipeline] { (Integration Check)
ERROR: service endpoint is not available
script returned exit code 1
[Pipeline] }""",
        rules_category="UNKNOWN",
    )

    assert result.confidence <= 0.35
    assert result.matched_evidence == [
        "ERROR: service endpoint is not available"
    ]