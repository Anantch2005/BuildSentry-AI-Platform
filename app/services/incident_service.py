import logging
import time
import uuid

from app.ai.classifier import AIClassifier
from app.classifier.classifier import FailureClassifier
from app.collectors.jenkins import JenkinsCollector
from app.database.repository import IncidentRepository
from app.models import (
    Incident,
    FailureClassification,
    RemediationResult,
)
from app.observability.telemetry import (
    ai_classifications_total,
    escalated_total,
    healed_total,
    incidents_total,
    policy_decisions_total,
    policy_denials_total,
    processing_duration,
    remediation_attempts_total,
    remediation_failure_total,
    remediation_success_total,
    tracer,
)
from app.policy.engine import PolicyEngine
from app.policy.models import PolicyDecision
from app.remediation.executor import RemediationExecutor
from app.safety.circuit_breaker import CircuitBreaker
from app.services.reporting import IncidentResultReporter

logger = logging.getLogger(__name__)


class IncidentService:
    def __init__(self):
        self.jenkins = JenkinsCollector()

        # Deterministic classifier remains authoritative.
        self.classifier = FailureClassifier()

        # Local Ollama diagnostic assistant.
        # AI is diagnostic only and never controls remediation.
        self.ai_classifier = AIClassifier()

        # Policy Engine remains authoritative.
        self.policy = PolicyEngine()

        # Remediation engine.
        self.remediation = RemediationExecutor()

        # Safety protection.
        self.circuit_breaker = CircuitBreaker(
            max_attempts=3,
            window_minutes=30,
        )

        # PostgreSQL persistence.
        self.repository = IncidentRepository()
        self.reporter = IncidentResultReporter(self.repository)

    @staticmethod
    def _log_lines(*parts) -> None:
        """Emit former console-summary lines through structured application logging."""
        logger.info("%s", " ".join(str(part) for part in parts))

    def _record_processing_duration(
        self,
        start_time: float,
        job_name: str,
    ):
        processing_duration.record(
            time.perf_counter() - start_time,
            {
                "job_name": job_name,
            },
        )



    async def _collect_and_classify(
        self,
        job_name: str,
        build_number: int,
    ):
        """Collect Jenkins evidence, classify deterministically, and run advisory AI."""
        # =========================================
        # 2. COLLECT EVIDENCE
        # =========================================

        with tracer.start_as_current_span(
            "autoheal.collect_evidence"
        ) as span:

            span.set_attribute(
                "jenkins.job.name",
                job_name,
            )

            span.set_attribute(
                "jenkins.build.number",
                build_number,
            )

            build = (
                await self.jenkins.get_build_info(
                    job_name,
                    build_number,
                )
            )

            log = build.console_log or ""

            span.set_attribute(
                "jenkins.build.result",
                build.result or "UNKNOWN",
            )

            span.set_attribute(
                "jenkins.console_log_length",
                len(log),
            )

        # =========================================
        # 3. RULE-BASED CLASSIFICATION
        # =========================================

        with tracer.start_as_current_span(
            "autoheal.classification.rules"
        ) as span:

            classification_data = (
                self.classifier.classify(log)
            )

            classification = (
                FailureClassification(
                    **classification_data
                )
            )

            span.set_attribute(
                "failure.category",
                classification.category,
            )

            span.set_attribute(
                "failure.action",
                classification.action,
            )

            span.set_attribute(
                "classifier.source",
                "rules",
            )

            span.set_attribute(
                "classifier.confidence",
                classification.confidence,
            )

        # =========================================
        # 4. AI DIAGNOSTIC ANALYSIS
        # =========================================

        #
        # Ollama analyzes EVERY failure.
        #
        # AI is advisory.
        #
        # Rules remain authoritative for known failures.
        #
        # Policy remains authoritative for remediation.
        #

        ai_was_used = False
        ai_result = None

        # IMPORTANT:
        # This deterministic category is passed to the AI
        # as context, but the AI cannot replace it.
        rules_category = classification.category

        if self.ai_classifier.enabled:
            self._log_lines("-" * 60)
            self._log_lines("AI DIAGNOSTIC ANALYSIS")
            self._log_lines("-" * 60)

            with tracer.start_as_current_span(
                "autoheal.ai_diagnosis"
            ) as span:

                try:
                    # IMPORTANT FIX:
                    #
                    # Pass the deterministic classification
                    # into the AI classifier.
                    #
                    # The AI classifier can now use the known
                    # failure family when selecting the strongest
                    # failure evidence from a large Jenkins log.
                    #
                    # The AI result is still diagnostic only.
                    ai_result = (
                        await self.ai_classifier.classify(
                            log,
                            rules_category=rules_category,
                        )
                    )

                    ai_was_used = True

                    ai_classifications_total.add(
                        1,
                        {
                            "category": rules_category,
                        },
                    )

                    span.set_attribute(
                        "ai.category",
                        ai_result.category,
                    )

                    span.set_attribute(
                        "ai.confidence",
                        ai_result.confidence,
                    )

                    span.set_attribute(
                        "ai.root_cause",
                        ai_result.root_cause,
                    )

                    span.set_attribute(
                        "ai.reasoning",
                        ai_result.reasoning,
                    )

                    span.set_attribute(
                        "ai.role",
                        "diagnostic",
                    )

                    span.set_attribute(
                        "rules.category",
                        rules_category,
                    )

                    self._log_lines(
                        f"AI Category  : "
                        f"{ai_result.category}"
                    )

                    self._log_lines(
                        f"AI Root Cause: "
                        f"{ai_result.root_cause}"
                    )

                    self._log_lines(
                        f"AI Reasoning : "
                        f"{ai_result.reasoning}"
                    )

                    self._log_lines(
                        f"AI Confidence: "
                        f"{ai_result.confidence}"
                    )

                    if ai_result.matched_evidence:
                        self._log_lines(
                            "AI Evidence  : "
                            + "; ".join(
                                ai_result.matched_evidence
                            )
                        )

                    if ai_result.recommendations:
                        self._log_lines(
                            "AI Checks    :"
                        )

                        for recommendation in (
                            ai_result.recommendations
                        ):
                            self._log_lines(
                                f"  - {recommendation}"
                            )

                    # -----------------------------------------
                    # AI IS ADVISORY
                    # -----------------------------------------

                    #
                    # IMPORTANT:
                    #
                    # Keep the deterministic classification.
                    #
                    # Do not replace CODE_FAILURE with whatever
                    # category Ollama happens to return.
                    #
                    # Do not allow Ollama to change policy.
                    #

                    ai_reasoning = (
                        self.reporter.format_ai_reasoning(
                            ai_result
                        )
                    )

                    classification = (
                        classification.model_copy(
                            update={
                                "ai_root_cause": (
                                    ai_result.root_cause
                                ),
                                "ai_reasoning": (
                                    ai_reasoning
                                ),
                                "ai_confidence": (
                                    ai_result.confidence
                                ),
                                "ai_evidence": (
                                    ai_result.matched_evidence
                                ),
                            }
                        )
                    )

                    if (
                        ai_result.category
                        != rules_category
                    ):
                        self._log_lines(
                            "AI/rules diagnostic difference: "
                            f"rules={rules_category}, "
                            f"ai={ai_result.category}"
                        )

                except Exception as exc:
                    span.record_exception(
                        exc
                    )

                    self._log_lines(
                        "AI diagnostic analysis failed: "
                        f"{exc}"
                    )

                    # AI failure must NEVER change the
                    # deterministic AutoHeal decision.

                    classification = (
                        classification.model_copy(
                            update={
                                "ai_root_cause": None,
                                "ai_reasoning": (
                                    "AI analysis unavailable: "
                                    f"{exc}"
                                ),
                                "ai_confidence": None,
                                "ai_evidence": [],
                            }
                        )
                    )

                    # Safe rule-based remediation continues
                    # exactly as before.

        return build, log, classification, ai_result, ai_was_used, rules_category

    def _persist_incident(
        self,
        incident: Incident,
        classification: FailureClassification,
        ai_was_used: bool,
        ai_result,
        rules_category: str,
        start_time: float,
    ) -> int | None:
        """Persist incident and diagnostic audit data, handling duplicate races."""
        # =========================================
        # 6. PERSIST INCIDENT
        # =========================================

        with tracer.start_as_current_span(
            "autoheal.persist_incident"
        ):
            database_incident_id = (
                self.repository.create_incident(
                    incident_id=incident.incident_id,
                    source=incident.source,
                    job_name=incident.job_name,
                    build_number=incident.build_number,
                    status=incident.status,
                    build_url=incident.build_url,
                    failure_category=(
                        classification.category
                    ),
                    failure_action=(
                        classification.action
                    ),
                    reason=classification.reason,
                    matched_pattern=(
                        classification.matched_pattern
                    ),
                    confidence=(
                        classification.confidence
                    ),
                    console_log=incident.console_log,
                    classifier_source=(
                        classification.source
                    ),
                    ai_root_cause=(
                        classification.ai_root_cause
                    ),
                    ai_reasoning=(
                        classification.ai_reasoning
                    ),
                    ai_confidence=(
                        classification.ai_confidence
                    ),
                )
            )

        if database_incident_id is None:
            self._log_lines(
                f"Duplicate incident ignored after unique-key conflict: "
                f"{incident.job_name} #{incident.build_number}"
            )
            self._record_processing_duration(start_time, incident.job_name)
            return None

        self.repository.add_audit_event(
            incident_id=database_incident_id,
            event_type="INCIDENT_CREATED",
            message=(
                f"Incident created for Jenkins "
                f"{incident.job_name} #{incident.build_number}"
            ),
        )

        # =========================================
        # 7. AI AUDIT
        # =========================================

        if ai_was_used and ai_result is not None:
            self.repository.add_audit_event(
                incident_id=database_incident_id,
                event_type="AI_ANALYSIS",
                message=(
                    f"rules_category="
                    f"{rules_category}; "
                    f"ai_category="
                    f"{ai_result.category}; "
                    f"confidence="
                    f"{ai_result.confidence}; "
                    f"root_cause="
                    f"{ai_result.root_cause}; "
                    f"reasoning="
                    f"{ai_result.reasoning}; "
                    f"evidence="
                    f"{'; '.join(ai_result.matched_evidence)}; "
                    f"recommendations="
                    f"{'; '.join(ai_result.recommendations)}"
                ),
            )

        # =========================================
        # LOG INCIDENT
        # =========================================

        self._log_lines()
        self._log_lines("=" * 60)
        self._log_lines("AUTOHEAL INCIDENT")
        self._log_lines("=" * 60)

        self._log_lines(
            f"Incident ID : "
            f"{incident.incident_id}"
        )

        self._log_lines(
            f"Job         : {incident.job_name}"
        )

        self._log_lines(
            f"Build       : #{incident.build_number}"
        )

        self._log_lines(
            f"Status      : {incident.status}"
        )

        self._log_lines("-" * 60)
        self._log_lines("CLASSIFICATION")
        self._log_lines("-" * 60)

        self._log_lines(
            f"Category    : "
            f"{classification.category}"
        )

        self._log_lines(
            f"Action      : "
            f"{classification.action}"
        )

        self._log_lines(
            f"Source      : "
            f"{classification.source}"
        )

        self._log_lines(
            f"Confidence  : "
            f"{classification.confidence}"
        )

        self._log_lines(
            f"Reason      : "
            f"{classification.reason}"
        )

        return database_incident_id

    def _evaluate_policy(
        self,
        classification: FailureClassification,
        database_incident_id: int,
    ) -> PolicyDecision:
        """Evaluate deterministic classification and persist the policy decision."""
        # =========================================
        # 8. POLICY ENGINE
        # =========================================

        with tracer.start_as_current_span(
            "autoheal.policy.evaluate"
        ) as span:

            policy = self.policy.evaluate(
                category=classification.category,
                classifier_action=classification.action,
            )

            policy_decisions_total.add(
                1,
                {
                    "category": policy.category,
                    "allowed": str(
                        policy.allowed
                    ),
                    "action": policy.action,
                    "risk": policy.risk_level,
                },
            )

            span.set_attribute(
                "policy.category",
                policy.category,
            )

            span.set_attribute(
                "policy.allowed",
                policy.allowed,
            )

            span.set_attribute(
                "policy.action",
                policy.action,
            )

            span.set_attribute(
                "policy.risk_level",
                policy.risk_level,
            )

            span.set_attribute(
                "policy.max_attempts",
                policy.max_attempts,
            )

        self._log_lines("-" * 60)
        self._log_lines("POLICY DECISION")
        self._log_lines("-" * 60)

        self._log_lines(
            f"Risk Level      : "
            f"{policy.risk_level}"
        )

        self._log_lines(
            f"Allowed         : "
            f"{policy.allowed}"
        )

        self._log_lines(
            f"Policy Action   : "
            f"{policy.action}"
        )

        self._log_lines(
            f"Max Attempts    : "
            f"{policy.max_attempts}"
        )

        self._log_lines(
            f"Approval Needed : "
            f"{policy.requires_approval}"
        )

        self._log_lines(
            f"Reason          : "
            f"{policy.reason}"
        )

        self.repository.add_audit_event(
            incident_id=database_incident_id,
            event_type="POLICY_EVALUATED",
            message=(
                f"category={policy.category}; "
                f"risk={policy.risk_level}; "
                f"allowed={policy.allowed}; "
                f"action={policy.action}; "
                f"max_attempts="
                f"{policy.max_attempts}; "
                f"approval="
                f"{policy.requires_approval}"
            ),
        )

        return policy

    def _handle_policy_denied(
        self,
        incident: Incident,
        classification: FailureClassification,
        policy: PolicyDecision,
        database_incident_id: int,
        build_number: int,
        job_name: str,
        start_time: float,
        root_span,
    ) -> Incident:
        """Record a denied policy decision without triggering a recovery build."""
        # =========================================
        # 9. POLICY DENIED
        # =========================================

        if not policy.allowed:
            policy_denials_total.add(
                1,
                {
                    "category": policy.category,
                    "reason": policy.action,
                },
            )

            escalated_total.add(
                1,
                {
                    "category": policy.category,
                    "action": policy.action,
                },
            )

            root_span.set_attribute(
                "result",
                "ESCALATED",
            )

            incident.remediation = (
                RemediationResult(
                    action=policy.action,
                    success=False,
                    message=policy.reason,
                )
            )

            self.repository.create_attempt(
                incident_id=database_incident_id,
                attempt_number=0,
                action=policy.action,
                success=False,
                message=policy.reason,
                original_build_number=build_number,
                retry_build_number=None,
                queue_url=None,
                verification_result=None,
            )

            self.repository.add_audit_event(
                incident_id=database_incident_id,
                event_type="POLICY_DENIED",
                message=policy.reason,
            )

            self.reporter.record_result_summary(
                incident=incident,
                classification=classification,
                policy=policy,
                result=incident.remediation,
                database_incident_id=database_incident_id,
            )

            self._log_lines("-" * 60)
            self._log_lines("POLICY RESULT")
            self._log_lines("-" * 60)

            self._log_lines(
                f"Decision    : "
                f"{policy.action}"
            )

            self._log_lines(
                f"Message     : "
                f"{policy.reason}"
            )

            self._log_lines("=" * 60)

            self._record_processing_duration(
                start_time,
                job_name,
            )

            return incident

    def _check_recovery_limits(
        self,
        job_name: str,
        build_number: int,
        start_time: float,
        root_span,
        incident: Incident,
        classification: FailureClassification,
        policy: PolicyDecision,
        database_incident_id: int,
    ) -> int | None:
        """Apply the per-incident attempt cap and the job/category circuit breaker."""
        # =========================================
        # 10. CIRCUIT BREAKER
        # =========================================

        with tracer.start_as_current_span(
            "autoheal.safety.circuit_breaker"
        ) as span:

            allowed = (
                self.circuit_breaker.allow(
                    job_name,
                    policy.category,
                )
            )

            circuit_attempt = self.circuit_breaker.count(
                job_name,
                policy.category,
            )
            # Policy max_attempts is per incident, not a count shared by
            # every incident for this job/category. The circuit breaker
            # remains a separate job/category-wide emergency guard.
            attempt = self.repository.count_attempts(
                database_incident_id
            ) + 1

            span.set_attribute(
                "circuit_breaker.allowed",
                allowed,
            )

            span.set_attribute(
                "circuit_breaker.attempt",
                circuit_attempt,
            )

        self._log_lines("-" * 60)
        self._log_lines("SAFETY CHECK")
        self._log_lines("-" * 60)

        self._log_lines(
            f"Incident remediation attempt : {attempt}/{policy.max_attempts}"
        )
        self._log_lines(
            f"Circuit breaker usage        : {circuit_attempt}/3"
        )
        self._log_lines(
            f"Allowed                      : {allowed}"
        )

        # =========================================
        # 11. POLICY ATTEMPT LIMIT
        # =========================================

        if attempt > policy.max_attempts:
            message = (
                f"Policy limit reached for "
                f"{policy.category}. "
                f"Maximum attempts: "
                f"{policy.max_attempts}."
            )

            escalated_total.add(
                1,
                {
                    "category": policy.category,
                    "action": "ESCALATE",
                },
            )

            root_span.set_attribute(
                "result",
                "ESCALATED",
            )

            incident.remediation = (
                RemediationResult(
                    action="ESCALATE",
                    success=False,
                    message=message,
                )
            )

            self.repository.create_attempt(
                incident_id=database_incident_id,
                attempt_number=attempt,
                action="ESCALATE",
                success=False,
                message=message,
                original_build_number=build_number,
                retry_build_number=None,
                queue_url=None,
                verification_result=None,
            )

            self.repository.add_audit_event(
                incident_id=database_incident_id,
                event_type="POLICY_LIMIT_REACHED",
                message=message,
            )

            self.reporter.record_result_summary(
                incident=incident,
                classification=classification,
                policy=policy,
                result=incident.remediation,
                database_incident_id=database_incident_id,
            )

            self._log_lines(
                "Decision    : ESCALATE"
            )

            self._log_lines(
                f"Message     : {message}"
            )

            self._log_lines("=" * 60)

            self._record_processing_duration(
                start_time,
                job_name,
            )

            return None

        # =========================================
        # 12. CIRCUIT BREAKER DENIED
        # =========================================

        if not allowed:
            message = (
                "Circuit breaker opened after "
                "repeated remediation attempts."
            )

            escalated_total.add(
                1,
                {
                    "category": policy.category,
                    "action": "ESCALATE",
                },
            )

            root_span.set_attribute(
                "result",
                "ESCALATED",
            )

            incident.remediation = (
                RemediationResult(
                    action="ESCALATE",
                    success=False,
                    message=message,
                )
            )

            self.repository.create_attempt(
                incident_id=database_incident_id,
                attempt_number=attempt,
                action="ESCALATE",
                success=False,
                message=message,
                original_build_number=build_number,
                retry_build_number=None,
                queue_url=None,
                verification_result=None,
            )

            self.repository.add_audit_event(
                incident_id=database_incident_id,
                event_type="CIRCUIT_BREAKER_OPEN",
                message=message,
            )

            self.reporter.record_result_summary(
                incident=incident,
                classification=classification,
                policy=policy,
                result=incident.remediation,
                database_incident_id=database_incident_id,
            )

            self._log_lines(
                "Decision    : ESCALATE"
            )

            self._log_lines("=" * 60)

            self._record_processing_duration(
                start_time,
                job_name,
            )

            return None

        return attempt

    async def _apply_policy_and_recover(
        self,
        job_name: str,
        build_number: int,
        start_time: float,
        root_span,
        incident: Incident,
        classification: FailureClassification,
        database_incident_id: int,
    ) -> Incident:
        """Evaluate policy, enforce limits, run an allowlisted recovery, and verify outcome."""
        policy = self._evaluate_policy(
            classification=classification,
            database_incident_id=database_incident_id,
        )
        if not policy.allowed:
            return self._handle_policy_denied(
                incident=incident,
                classification=classification,
                policy=policy,
                database_incident_id=database_incident_id,
                build_number=build_number,
                job_name=job_name,
                start_time=start_time,
                root_span=root_span,
            )

        attempt = self._check_recovery_limits(
            job_name=job_name,
            build_number=build_number,
            start_time=start_time,
            root_span=root_span,
            incident=incident,
            classification=classification,
            policy=policy,
            database_incident_id=database_incident_id,
        )
        if attempt is None:
            return incident

        # =========================================
        # 13. POLICY-CONTROLLED REMEDIATION
        # =========================================

        self._log_lines("-" * 60)
        self._log_lines(
            "POLICY-CONTROLLED REMEDIATION"
        )
        self._log_lines("-" * 60)

        self._log_lines(
            f"Policy Action : "
            f"{policy.action}"
        )

        self._log_lines(
            f"Risk Level    : "
            f"{policy.risk_level}"
        )

        self._log_lines(
            f"Max Attempts  : "
            f"{policy.max_attempts}"
        )

        self.repository.add_audit_event(
            incident_id=database_incident_id,
            event_type="REMEDIATION_STARTED",
            message=(
                f"Attempt {attempt}: "
                f"{policy.action}"
            ),
        )

        remediation_attempts_total.add(
            1,
            {
                "category": policy.category,
                "action": policy.action,
            },
        )

        with tracer.start_as_current_span(
            "autoheal.remediation"
        ) as span:

            span.set_attribute(
                "remediation.category",
                policy.category,
            )

            span.set_attribute(
                "remediation.action",
                policy.action,
            )

            span.set_attribute(
                "remediation.attempt",
                attempt,
            )

            try:
                result = (
                    await self.remediation.execute(
                        job_name=job_name,
                        category=policy.category,
                        action=policy.action,
                    )
                )

            except Exception as exc:
                span.record_exception(exc)

                result = {
                    "action": "ESCALATE",
                    "success": False,
                    "message": (
                        f"Remediation exception: "
                        f"{exc}"
                    ),
                }

        # =========================================
        # 14. STORE REMEDIATION RESULT
        # =========================================

        incident.remediation = (
            RemediationResult(
                action=result["action"],
                success=result["success"],
                message=result["message"],
                new_build_number=result.get(
                    "new_build_number"
                ),
                verification_result=result.get(
                    "verification_result"
                ),
                queue_url=result.get(
                    "queue_url"
                ),
            )
        )

        self.repository.create_attempt(
            incident_id=database_incident_id,
            attempt_number=attempt,
            action=result["action"],
            success=result["success"],
            message=result["message"],
            original_build_number=build_number,
            retry_build_number=result.get(
                "new_build_number"
            ),
            queue_url=result.get(
                "queue_url"
            ),
            verification_result=result.get(
                "verification_result"
            ),
        )

        # =========================================
        # 15. AUDIT RESULT
        # =========================================

        event_type = (
            "REMEDIATION_SUCCEEDED"
            if result["success"]
            else "REMEDIATION_ESCALATED"
        )

        self.repository.add_audit_event(
            incident_id=database_incident_id,
            event_type=event_type,
            message=result["message"],
        )

        # =========================================
        # 16. METRICS RESULT
        # =========================================

        if result["success"]:
            remediation_success_total.add(
                1,
                {
                    "category": policy.category,
                    "action": result["action"],
                },
            )

            healed_total.add(
                1,
                {
                    "category": policy.category,
                },
            )

            root_span.set_attribute(
                "result",
                "HEALED",
            )

            root_span.set_attribute(
                "remediation.success",
                True,
            )

        else:
            remediation_failure_total.add(
                1,
                {
                    "category": policy.category,
                    "action": result["action"],
                },
            )

            escalated_total.add(
                1,
                {
                    "category": policy.category,
                    "action": result["action"],
                },
            )

            root_span.set_attribute(
                "result",
                "ESCALATED",
            )

            root_span.set_attribute(
                "remediation.success",
                False,
            )

        # =========================================
        # 17. LOG RESULT
        # =========================================

        self._log_lines("-" * 60)
        self._log_lines("REMEDIATION RESULT")
        self._log_lines("-" * 60)

        self._log_lines(
            f"Action  : "
            f"{result['action']}"
        )

        self._log_lines(
            f"Success : "
            f"{result['success']}"
        )

        self._log_lines(
            f"Message : "
            f"{result['message']}"
        )

        if result.get("queue_url"):
            self._log_lines(
                f"Queue   : "
                f"{result['queue_url']}"
            )

        if result.get(
            "new_build_number"
        ):
            self._log_lines(
                f"New Build : "
                f"#{result['new_build_number']}"
            )

        if result.get(
            "verification_result"
        ):
            self._log_lines(
                f"Verified : "
                f"{result['verification_result']}"
            )

        # =========================================
        # 18. AUTOHEAL RESULT SUMMARY
        # =========================================

        self.reporter.record_result_summary(
            incident=incident,
            classification=classification,
            policy=policy,
            result=incident.remediation,
            database_incident_id=database_incident_id,
        )

        # =========================================
        # 19. RESET CIRCUIT AFTER SUCCESS
        # =========================================

        if result["success"]:
            self.circuit_breaker.reset(
                job_name,
                policy.category,
            )

            self._log_lines(
                "Result : HEALED"
            )

        else:
            self._log_lines(
                "Result : ESCALATE"
            )

        self._log_lines("=" * 60)

        self._record_processing_duration(
            start_time,
            job_name,
        )

        return incident

    async def process_failure(
        self,
        job_name: str,
        build_number: int,
    ) -> Incident | None:
        start_time = time.perf_counter()

        # =========================================
        # OBSERVABILITY — INCIDENT RECEIVED
        # =========================================

        incidents_total.add(
            1,
            {
                "source": "jenkins",
                "job_name": job_name,
            },
        )

        with tracer.start_as_current_span(
            "autoheal.process_failure"
        ) as root_span:

            root_span.set_attribute(
                "service.name",
                "autoheal",
            )

            root_span.set_attribute(
                "jenkins.job.name",
                job_name,
            )

            root_span.set_attribute(
                "jenkins.build.number",
                build_number,
            )

            # =========================================
            # 1. PERSISTENT DUPLICATE PROTECTION
            # =========================================

            with tracer.start_as_current_span(
                "autoheal.duplicate_check"
            ):
                if self.repository.exists(
                    job_name,
                    build_number,
                ):
                    root_span.set_attribute(
                        "incident.duplicate",
                        True,
                    )

                    self._log_lines(
                        f"Duplicate incident ignored: "
                        f"{job_name} #{build_number}"
                    )

                    self._record_processing_duration(
                        start_time,
                        job_name,
                    )

                    return None

            (
                build,
                log,
                classification,
                ai_result,
                ai_was_used,
                rules_category,
            ) = await self._collect_and_classify(job_name, build_number)

            # =========================================
            # 5. CREATE INCIDENT
            # =========================================

            incident = Incident(
                incident_id=(
                    f"AH-{uuid.uuid4().hex[:8].upper()}"
                ),
                source="jenkins",
                job_name=job_name,
                build_number=build_number,
                status=build.result or "UNKNOWN",
                build_url=build.url,
                # Persist only bounded, redacted failure evidence. The full log
                # remains in memory for deterministic classification and AI extraction,
                # but is not written to PostgreSQL by default.
                console_log=self.ai_classifier._prepare_log(
                    log,
                    rules_category=classification.category,
                ),
                classification=classification,
            )

            root_span.set_attribute(
                "incident.id",
                incident.incident_id,
            )

            root_span.set_attribute(
                "failure.category",
                classification.category,
            )

            root_span.set_attribute(
                "classifier.source",
                classification.source,
            )

            database_incident_id = self._persist_incident(
                incident=incident,
                classification=classification,
                ai_was_used=ai_was_used,
                ai_result=ai_result,
                rules_category=rules_category,
                start_time=start_time,
            )
            if database_incident_id is None:
                return None

            return await self._apply_policy_and_recover(
                job_name=job_name,
                build_number=build_number,
                start_time=start_time,
                root_span=root_span,
                incident=incident,
                classification=classification,
                database_incident_id=database_incident_id,
            )