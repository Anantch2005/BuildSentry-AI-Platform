"""Focused Jenkins log extraction and local Ollama diagnostics.

Ollama is advisory only. It never chooses remediation or policy.
"""
from __future__ import annotations

import json
import logging
import re
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
            "excerpt_chars=%d category=%s selected_stage=%s dependency_marker=%s "
            "missing_module_marker=%s package_resolution_marker=%s",
            self.model,
            len(original_log),
            len(excerpt),
            rules_category or "UNKNOWN",
            self._selected_stage(excerpt),
            "AUTOHEAL_DEPENDENCY_FAILURE" in excerpt,
            bool(re.search(r"No module named|ModuleNotFoundError", excerpt, re.I)),
            bool(re.search(r"No matching distribution found|Could not find a version that satisfies|ResolutionImpossible|ERESOLVE", excerpt, re.I)),
        )

        result = await self._generate_diagnosis(prompt)

        # Require at least one quotation from the excerpt actually sent to
        # Ollama. Small local models sometimes paraphrase a real log line. Give
        # the model one constrained repair attempt before marking the result
        # unverified; never accept unsupported evidence as if it were a quote.
        supported_evidence = self._supported_evidence(
            result.matched_evidence,
            excerpt,
        )

        if not supported_evidence:
            logger.warning(
                "Ollama evidence did not match targeted excerpt; requesting "
                "one evidence-only correction. category=%s excerpt_chars=%d",
                rules_category or "UNKNOWN",
                len(excerpt),
            )
            correction_prompt = (
                "Your previous response cited evidence that was not copied "
                "verbatim from the log excerpt. Re-analyze ONLY the exact "
                "excerpt below. Choose one short line or contiguous phrase "
                "that appears exactly in the excerpt, preserving its words. "
                "Do not paraphrase, normalize, or invent a quotation. If no "
                "concrete failure line exists, use an empty matched_evidence "
                "array and set root_cause to unverified. Return only the same "
                "JSON schema.\n\n" + prompt
            )
            result = await self._generate_diagnosis(correction_prompt)
            supported_evidence = self._supported_evidence(
                result.matched_evidence,
                excerpt,
            )

        if not supported_evidence:
            logger.warning(
                "Ollama returned no evidence matching targeted excerpt after correction; "
                "trying deterministic evidence fallback. category=%s",
                rules_category or "UNKNOWN",
            )
            fallback = self._deterministic_evidence_fallback(
                excerpt, rules_category=rules_category
            )
            if fallback is not None:
                return fallback
            return AIClassification(
                category="unverified_diagnosis",
                root_cause=(
                    "The AI could not provide a root cause supported by an exact log excerpt."
                ),
                reasoning=(
                    "Ollama did not cite a verbatim line from the targeted excerpt, and no "
                    "known deterministic error signature was available for a safe fallback. "
                    "This does not prove Jenkins failed to collect the log. Compare the "
                    "targeted evidence with the original failed-stage console output."
                ),
                confidence=0.0,
                matched_evidence=[],
                recommendations=[
                    "Compare the targeted evidence excerpt with the original Jenkins console for this exact failed build.",
                    "If the concrete error is absent from the excerpt, fix log/stage extraction; if present, check the configured Ollama model's verbatim-evidence behavior.",
                ],
            )

        # Require at least one quotation from the excerpt actually sent to
        # Ollama. This validates the quote, not the semantic correctness of
        # the model's interpretation.
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

    @staticmethod
    def _selected_stage(excerpt: str) -> str:
        match = re.search(r"(?im)^Failed Jenkins stage:\s*(.+)$", excerpt or "")
        return match.group(1).strip()[:100] if match else "UNKNOWN"

    @classmethod
    def _deterministic_evidence_fallback(
        cls, excerpt: str, rules_category: str | None = None
    ) -> AIClassification | None:
        """Safe, transparent fallback for specific signatures after AI quote failure."""
        lines = [line.strip() for line in (excerpt or "").splitlines() if line.strip()]
        lines = [line for line in lines if not FailureContextExtractor._is_skipped_stage_line(line)]

        marker = next((line for line in lines if "AUTOHEAL_DEPENDENCY_FAILURE" in line), None)
        if marker:
            return AIClassification(
                category="synthetic_dependency_failure_injection",
                root_cause=(
                    "The demo deliberately emitted AUTOHEAL_DEPENDENCY_FAILURE and exited non-zero. "
                    "This confirms the injected test path, not a real package-resolution error."
                ),
                reasoning=(
                    "Ollama did not return a verifiable quotation. This fallback uses the exact "
                    "injected marker in the targeted excerpt; it does not identify a missing "
                    "package or version conflict."
                ),
                confidence=0.95,
                matched_evidence=[marker],
                recommendations=[
                    "Use a genuine missing-module or package-resolution error for a realistic dependency-diagnosis test.",
                    "Keep remediation authorization in the deterministic policy engine."
                ],
            )

        for line in lines:
            match = re.search(r"No module named\s*['\"]?([A-Za-z0-9_.-]+)", line, re.I)
            if match or "ModuleNotFoundError" in line:
                module = match.group(1).rstrip("'\".,:;") if match else "the imported module"
                return AIClassification(
                    category="missing_python_module",
                    root_cause=(
                        f"Python module '{module}' is unavailable in the interpreter used by the failing Jenkins stage."
                    ),
                    reasoning=(
                        "The targeted log explicitly reports a missing Python module. This establishes an "
                        "environment/import problem, but not why the dependency was absent. This is a "
                        "deterministic fallback because Ollama's quotation could not be verified."
                    ),
                    confidence=0.90,
                    matched_evidence=[line],
                    recommendations=[
                        "Confirm dependency installation and test execution use the same Python interpreter.",
                        "Check the requirements/lock file and ensure installation completed before tests ran."
                    ],
                )

        for line in lines:
            lower = line.casefold()
            if "no matching distribution found" in lower or "could not find a version that satisfies" in lower:
                return AIClassification(
                    category="python_package_resolution_failure",
                    root_cause=(
                        "pip could not find a distribution matching the requested package/version in the configured package index and interpreter environment."
                    ),
                    reasoning=(
                        "The log explicitly reports no matching distribution/version. It may not distinguish a bad version, "
                        "unsupported Python/platform, or package-index configuration."
                    ),
                    confidence=0.88,
                    matched_evidence=[line],
                    recommendations=[
                        "Check the exact package requirement and supported Python versions.",
                        "Check package-index configuration and platform compatibility."
                    ],
                )
            if "resolutionimpossible" in lower or "eresolve" in lower or "dependency conflict" in lower:
                return AIClassification(
                    category="package_dependency_conflict",
                    root_cause="The package manager reports incompatible dependency constraints.",
                    reasoning=(
                        "The targeted log contains an explicit dependency-conflict signature. Read the surrounding "
                        "resolver output to identify the conflicting packages and versions."
                    ),
                    confidence=0.82,
                    matched_evidence=[line],
                    recommendations=[
                        "Inspect resolver output for the conflicting package/version constraints.",
                        "Resolve constraints in the manifest or lock file rather than blindly upgrading dependencies."
                    ],
                )
        return None

    async def _generate_diagnosis(self, prompt: str) -> AIClassification:
        """Call Ollama once and parse its structured diagnostic response."""
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
                timeout=httpx.Timeout(self.timeout_seconds, connect=10.0)
            ) as client:
                response = await client.post(
                    f"{self.ollama_url}/api/generate",
                    json=payload,
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Ollama request failed: {exc}") from exc

        body = response.json()
        raw_response = body.get("response") if isinstance(body, dict) else None
        if not raw_response:
            raise RuntimeError("Ollama returned an empty response.")
        return self._parse_response(raw_response)

    @classmethod
    def _supported_evidence(
        cls,
        evidence: list[str],
        excerpt: str,
    ) -> list[str]:
        """Accept only quoted text that is actually present in the sent excerpt."""
        normalized_excerpt = cls._normalise_for_compare(excerpt).casefold()
        supported = []
        for item in evidence:
            normalized_item = cls._normalise_for_compare(item).casefold()
            if normalized_item and normalized_item in normalized_excerpt:
                supported.append(item)
        return supported

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