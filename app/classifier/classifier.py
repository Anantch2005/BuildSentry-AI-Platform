import re

from app.classifier.rules import FAILURE_RULES


class FailureClassifier:

    def classify(self, log: str) -> dict:
        if not log:
            return {
                "category": "UNKNOWN",
                "action": "ESCALATE",
                "reason": "No Jenkins console log was available.",
                "confidence": 0.0,
                "matched_pattern": None,
            }

        for rule in FAILURE_RULES:
            for pattern in rule.patterns:
                if re.search(pattern, log, re.IGNORECASE):
                    return {
                        "category": rule.category,
                        "action": rule.action,
                        "reason": rule.reason,
                        "confidence": rule.confidence,
                        "matched_pattern": pattern,
                    }

        return {
            "category": "UNKNOWN",
            "action": "ESCALATE",
            "reason": "No known failure signature matched the Jenkins log.",
            "confidence": 0.0,
            "matched_pattern": None,
        }