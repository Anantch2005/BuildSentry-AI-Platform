"""Focused Jenkins log extraction and local Ollama diagnostics.

Ollama is advisory only. It never chooses remediation or policy.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.ai.context import FailureContextExtractor
from app.ai.models import AIClassification
from app.ai.prompt import SYSTEM_PROMPT, build_user_prompt
from app.config import settings

logger = logging.getLogger(__name__)


class AIClassifier(FailureContextExtractor):
    """Send a bounded, redacted Jenkins failure excerpt to Ollama."""

    def __init__(self) -> None:
        self.enabled = settings.ai_enabled
        self.ollama_url = settings.ollama_url.rstrip("/")
        self.model = settings.ollama_model
        self.max_log_chars = settings.ai_max_log_chars
        self.timeout_seconds = settings.ai_timeout_seconds
        self.failure_context_lines = settings.ai_failure_context_lines
        self.max_stage_chars = settings.ai_stage_max_chars
        self.max_failure_candidates = settings.ai_max_failure_candidates

    async def classify(
        self, log: str, rules_category: str | None = None
    ) -> AIClassification:
        if not self.enabled:
            raise RuntimeError("AI analysis is disabled.")

        original_log = log or ""
        if not original_log.strip():
            raise RuntimeError("Jenkins console log is empty.")

        # Process the full console locally; only this focused excerpt reaches Ollama.
        excerpt = self._prepare_log(
            original_log,
            rules_category=rules_category,
        )
        prompt = build_user_prompt(
            excerpt,
            rules_category=rules_category,
        )

        logger.info(
            "Preparing Ollama diagnosis: model=%s original_chars=%d "
            "excerpt_chars=%d category=%s",
            self.model,
            len(original_log),
            len(excerpt),
            rules_category or "UNKNOWN",
        )

        payload = {
            "model": self.model,
            "system": SYSTEM_PROMPT,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": 0.1,
                "top_p": 0.9,
                "num_ctx": 8192,
            },
        }

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(
                    self.timeout_seconds,
                    connect=10.0,
                )
            ) as client:
                response = await client.post(
                    f"{self.ollama_url}/api/generate",
                    json=payload,
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"Ollama request failed: {exc}"
            ) from exc

        body = response.json()
        raw_response = (
            body.get("response")
            if isinstance(body, dict)
            else None
        )

        if not raw_response:
            raise RuntimeError("Ollama returned an empty response.")

        result = self._parse_response(raw_response)

        # Require at least one quotation from the excerpt actually sent to
        # Ollama. This validates the quote, not the semantic correctness of
        # the model's interpretation.
        normalized_excerpt = self._normalise_for_compare(
            excerpt
        ).casefold()

        supported_evidence = [
            item
            for item in result.matched_evidence
            if (
                self._normalise_for_compare(item).casefold()
                and self._normalise_for_compare(item).casefold()
                in normalized_excerpt
            )
        ]

        if not supported_evidence:
            logger.warning(
                "Ollama returned no evidence matching the supplied excerpt; "
                "diagnosis is unverified."
            )
            return AIClassification(
                category="unverified_diagnosis",
                root_cause=(
                    "The AI could not provide a root cause supported by "
                    "an exact log excerpt."
                ),
                reasoning=(
                    "The model's cited evidence did not match the targeted "
                    "Jenkins logs. The root cause is unverified; inspect "
                    "the failed stage's original console output."
                ),
                confidence=0.0,
                matched_evidence=[],
                recommendations=[
                    "Inspect the error lines in the failed Jenkins stage.",
                    (
                        "Check the first concrete error before the generic "
                        "exit-code message."
                    ),
                ],
            )

        # A diagnosis that explicitly admits uncertainty must not retain a
        # high numeric confidence. This keeps Grafana's confidence aligned
        # with the written explanation.
        uncertainty_text = (
            f"{result.root_cause} {result.reasoning}"
        ).casefold()

        uncertainty_markers = (
            "unverified",
            "not established",
            "not specified",
            "cannot determine",
            "can't determine",
            "could not determine",
            "couldn't determine",
            "insufficient evidence",
            "not enough evidence",
            "unclear",
            "does not show a clear root cause",
            "cause is not specified",
            "cannot be determined",
            "not enough information",
            "not clear",
            "unknown cause",
        )

        confidence = result.confidence
        if any(
            marker in uncertainty_text
            for marker in uncertainty_markers
        ):
            confidence = min(confidence, 0.35)

        return result.model_copy(
            update={
                "matched_evidence": supported_evidence,
                "confidence": confidence,
            }
        )

    def _parse_response(
        self,
        raw_response: str,
    ) -> AIClassification:
        try:
            data = json.loads(raw_response)
        except json.JSONDecodeError as exc:
            data = self._extract_json_object(raw_response)
            if data is None:
                raise RuntimeError(
                    f"Ollama returned invalid JSON: {exc}"
                ) from exc

        if not isinstance(data, dict):
            raise RuntimeError(
                "Ollama JSON response was not an object."
            )

        return AIClassification(
            category=(
                self._clean_string(
                    data.get("category", "unknown")
                )
                or "unknown"
            ),
            root_cause=(
                self._clean_string(data.get("root_cause", ""))
                or "The AI did not provide a root cause."
            ),
            reasoning=(
                self._clean_string(data.get("reasoning", ""))
                or "The AI did not provide additional reasoning."
            ),
            confidence=self._normalize_confidence(
                data.get("confidence", 0.0)
            ),
            matched_evidence=self._normalize_list(
                data.get("matched_evidence", [])
            ),
            recommendations=self._normalize_list(
                data.get("recommendations", [])
            ),
        )

    @staticmethod
    def _extract_json_object(
        text: str,
    ) -> dict[str, Any] | None:
        start = text.find("{")
        end = text.rfind("}")

        if start < 0 or end <= start:
            return None

        try:
            value = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None

        return value if isinstance(value, dict) else None

    @staticmethod
    def _clean_string(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        return str(value).strip()

    @staticmethod
    def _normalize_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str) or not isinstance(value, list):
            value = [value]

        return [
            str(item).strip()
            for item in value
            if item is not None and str(item).strip()
        ][:10]

    @staticmethod
    def _normalize_confidence(value: Any) -> float:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return 0.0

        # Some models return 90 instead of 0.90.
        if confidence > 1.0:
            confidence /= 100.0

        return max(0.0, min(1.0, confidence))