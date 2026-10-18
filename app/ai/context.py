"""Focused extraction and redaction of Jenkins failure evidence for AI diagnostics.

This module only selects diagnostic context. It contains no remediation or policy logic.
"""

import re


class FailureContextExtractor:
    """Select a bounded, redacted failure window from a Jenkins console."""

    FAILURE_SIGNALS = (
        # ---------------------------------------------------------
        # Jenkins / Pipeline
        # ---------------------------------------------------------

        "script returned exit code",
        "process apparently never started",
        "hudson.AbortException",
        "ERROR:",
        "ERROR ",
        "FATAL:",
        "FAILED",
        "FAILURE",
        "BUILD FAILED",
        "BUILD FAILURE",
        "ABORTED",

        # ---------------------------------------------------------
        # Python / application / tests
        # ---------------------------------------------------------

        "AssertionError",
        "AssertionError:",
        "Traceback (most recent call last)",
        "Exception:",
        "Error:",
        "SyntaxError",
        "TypeError",
        "NameError",
        "KeyError",
        "ValueError",
        "RuntimeError",
        "ModuleNotFoundError",
        "ImportError",

        # Java
        "NoClassDefFoundError",
        "ClassNotFoundException",

        # Test frameworks
        "FAILED tests/",
        "FAIL tests/",
        "test failed",
        "tests failed",

        # ---------------------------------------------------------
        # Dependency / package managers
        # ---------------------------------------------------------

        "Could not find a version that satisfies",
        "No matching distribution found",
        "ResolutionImpossible",
        "dependency conflict",
        "failed to resolve dependencies",
        "npm ERR!",
        "npm error",
        "ERESOLVE",
        "yarn error",
        "pnpm ERR",
        "Could not resolve",
        "could not resolve",
        "Gradle build failed",
        "Could not resolve all files",
        "Could not resolve dependencies",
        "go: module",

        # ---------------------------------------------------------
        # Docker / containers
        # ---------------------------------------------------------

        "failed to solve:",
        "failed to build",
        "Cannot connect to the Docker daemon",
        "Docker daemon",
        "BuildKit",
        "docker build",
        "failed to create",
        "failed to start container",
        "OCI runtime",
        "containerd",

        # ---------------------------------------------------------
        # Network / DNS / TLS
        # ---------------------------------------------------------

        "Connection refused",
        "connection refused",
        "Connection reset",
        "connection reset",
        "Connection timed out",
        "connection timed out",
        "ConnectTimeout",
        "ReadTimeout",
        "Temporary failure in name resolution",
        "Could not resolve host",
        "Could not resolve",
        "network is unreachable",
        "Network is unreachable",
        "Name or service not known",
        "Failed to connect",
        "timed out",
        "SSL certificate",
        "CERTIFICATE_VERIFY_FAILED",
        "TLS handshake",
        "HTTP/1.1 4",
        "HTTP/1.1 5",
        "HTTP/2 4",
        "HTTP/2 5",

        # ---------------------------------------------------------
        # Registry / authentication
        # ---------------------------------------------------------

        "unauthorized",
        "Unauthorized",
        "authentication required",
        "requested access to the resource is denied",
        "manifest unknown",
        "denied:",
        "denied",
        "failed to push",
        "failed to pull",
        "toomanyrequests",

        # ---------------------------------------------------------
        # Filesystem / workspace / Jenkins agent
        # ---------------------------------------------------------

        "permission denied",
        "PermissionError",
        "No such file",
        "No such file or directory",
        "cannot create",
        "unable to create",
        "agent is offline",
        "channel is closed",
        "remoting.ChannelClosedException",
        "workspace",

        # Jenkins Git / SCM
        "Selected Git installation does not exist",
        "Git installation does not exist",
        "Git executable not found",
        "git: command not found",
        "The recommended git tool is: NONE",
        "Could not checkout",
        "Maximum checkout retry attempts reached",
        "fatal: not a git repository",
        "fatal: unable to access",
        "couldn't find remote ref",
        "checkout failed",

        # ---------------------------------------------------------
        # Kubernetes
        # ---------------------------------------------------------

        "ImagePullBackOff",
        "ErrImagePull",
        "CrashLoopBackOff",
        "CreateContainerConfigError",
        "FailedScheduling",
        "Back-off restarting failed container",

        # ---------------------------------------------------------
        # Terraform
        # ---------------------------------------------------------

        "terraform: command not found",
        "Error acquiring the state lock",
        "Error locking state",
        "Error: Failed to",
        "Error: Invalid",
        "Error: Unsupported",

        # ---------------------------------------------------------
        # Security / quality tools
        # ---------------------------------------------------------

        "SonarQube analysis failed",
        "QUALITY GATE STATUS: ERROR",
        "Quality Gate failed",
        "Trivy scan failed",
        "CRITICAL",
        "HIGH vulnerabilities",
    )

    CATEGORY_SIGNALS = {
        "CODE_FAILURE": (
            "AssertionError",
            "FAILED",
            "Traceback",
            "SyntaxError",
            "TypeError",
            "NameError",
            "KeyError",
            "ValueError",
            "ModuleNotFoundError",
            "ImportError",
            "test_",
        ),

        "DEPENDENCY_FAILURE": (
            "Could not find a version that satisfies",
            "No matching distribution found",
            "ResolutionImpossible",
            "dependency conflict",
            "package conflict",
            "version conflict",
            "failed to resolve dependencies",
            "pip install",
            "npm ERR!",
            "ERESOLVE",
            "Could not resolve dependencies",
        ),

        "DOCKER_FAILURE": (
            "failed to solve:",
            "failed to build",
            "Cannot connect to the Docker daemon",
            "Docker daemon",
            "BuildKit",
            "failed to create",
            "docker build",
        ),

        "REGISTRY_FAILURE": (
            "requested access to the resource is denied",
            "unauthorized",
            "Unauthorized",
            "authentication required",
            "manifest unknown",
            "denied:",
            "failed to push",
            "failed to pull",
            "toomanyrequests",
            "registry",
        ),

        "NETWORK_FAILURE": (
            "Connection timed out",
            "ConnectTimeout",
            "ReadTimeout",
            "connection refused",
            "Temporary failure in name resolution",
            "network is unreachable",
            "Failed to connect",
            "Could not resolve",
            "Name or service not known",
            "CERTIFICATE_VERIFY_FAILED",
        ),

        "WORKSPACE_FAILURE": (
            "unable to create file",
            "workspace",
            "permission denied",
            "cannot create",
            "Could not checkout",
            "Maximum checkout retry attempts reached",
            "fatal: cannot create directory",
            "No such file or directory",
        ),

        "FLAKY_TEST": (
            "AUTOHEAL_FLAKY_TEST",
        ),
    }

    # Jenkins console lines may be plain, time-prefixed, or ISO-timestamped.
    # Accept those prefixes so stage scoping works with real console output.
    STAGE_PATTERN = re.compile(
        r"^\s*(?:(?:\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?|\[[^\]\r\n]+\])\s+)?"
        r"\[Pipeline\]\s+\{\s+\((?P<stage>.+?)\)\s*$"
    )

    def _prepare_log(
        self,
        log: str,
        rules_category: str | None = None,
    ) -> str:

        lines = (
            log or ""
        ).splitlines()

        if not lines:

            return (
                "No Jenkins log lines were available."
            )

        # ---------------------------------------------------------
        # 1. Find candidate failure evidence
        # ---------------------------------------------------------

        candidates = [
            index
            for index, line in enumerate(lines)
            if self._is_failure_line(
                line,
                rules_category,
            )
        ]

        # Keep the most recent useful candidates.
        candidates = candidates[
            -self.max_failure_candidates:
        ]

        # ---------------------------------------------------------
        # 2. Select strongest failure
        # ---------------------------------------------------------

        failure_index = (
            self._select_failure_index(
                lines,
                candidates,
                rules_category,
            )
        )

        # ---------------------------------------------------------
        # 3. Find Jenkins stage
        # ---------------------------------------------------------

        (
            stage_name,
            stage_start,
            stage_end,
        ) = self._find_stage_for_failure(
            lines,
            failure_index,
        )

        # ---------------------------------------------------------
        # 4. Stage-local context
        # ---------------------------------------------------------

        if stage_start is not None:

            stage_lines = lines[
                stage_start:stage_end
            ]

            relative_failure_index = max(
                0,
                failure_index - stage_start,
            )

            stage_context = (
                self._trim_stage_around_failure(
                    stage_lines,
                    relative_failure_index,
                )
            )

            stage_text = "\n".join(
                stage_context
            )

        else:

            stage_text = self._window(
                lines,
                failure_index,
                self.failure_context_lines,
            )

        # ---------------------------------------------------------
        # 5. Failure-centered context
        # ---------------------------------------------------------

        window_start = max(
            0,
            failure_index
            - self.failure_context_lines,
        )

        window_end = min(
            len(lines),
            failure_index
            + self.failure_context_lines
            + 1,
        )

        # Never cross a known stage boundary.
        if stage_start is not None:

            window_start = max(
                window_start,
                stage_start,
            )

            window_end = min(
                window_end,
                stage_end,
            )

        focused_text = "\n".join(
            lines[
                window_start:window_end
            ]
        )

        # ---------------------------------------------------------
        # 6. Keep AI evidence inside the failed stage
        # ---------------------------------------------------------

        if stage_name and stage_text.strip():
            # Stage-local context is authoritative when available.
            # Do not append a second window that could contain
            # unrelated build stages.
            evidence_sections = [stage_text]

        elif stage_text.strip():
            evidence_sections = [stage_text]

        else:
            evidence_sections = [focused_text]
            
        # ---------------------------------------------------------
        # 7. Build AI evidence document
        # ---------------------------------------------------------

        output = [
            "===== AUTOHEAL TARGETED AI EVIDENCE =====",
            "",
            (
                "The complete Jenkins console was "
                "analyzed locally by AutoHeal."
            ),
            (
                "Only deterministic, failure-focused "
                "evidence is provided to Ollama."
            ),
            "",
            (
                "Rules category: "
                f"{rules_category or 'UNKNOWN'}"
            ),
        ]

        if stage_name:

            output.extend(
                [
                    "",
                    (
                        "Failed Jenkins stage: "
                        f"{stage_name}"
                    ),
                ]
            )

        output.extend(
            [
                "",
                "===== FAILURE EVIDENCE =====",
                "",
            ]
        )

        output.extend(
            evidence_sections
        )

        output.extend(
            [
                "",
                "===== END TARGETED EVIDENCE =====",
            ]
        )

        prepared = "\n".join(
            output
        )

        # ---------------------------------------------------------
        # 8. Secret redaction
        # ---------------------------------------------------------

        prepared = (
            self._redact_secrets(
                prepared
            )
        )

        # ---------------------------------------------------------
        # 9. Final hard limit
        # ---------------------------------------------------------

        return self._hard_limit(
            prepared
        )

    def _select_failure_index(
        self,
        lines: list[str],
        candidate_indexes: list[int],
        rules_category: str | None,
    ) -> int:

        if not candidate_indexes:

            return max(
                0,
                len(lines) - 1,
            )

        signals = (
            self.CATEGORY_SIGNALS.get(
                rules_category or "",
                self.FAILURE_SIGNALS,
            )
        )

        best_index = (
            candidate_indexes[-1]
        )

        best_score = -10**9

        for index in candidate_indexes:

            text = lines[index].strip()

            lower = text.lower()

            score = 0

            # Category-specific evidence.
            for signal in signals:

                if (
                    signal.lower()
                    in lower
                ):

                    score += 8

            # Strong root-cause markers.
            strong_markers = (
                "assertionerror",
                "traceback",
                "syntaxerror",
                "typeerror",
                "modulenotfounderror",
                "no matching distribution",
                "could not find a version",
                "resolutionimpossible",
                "failed to solve:",
                "cannot connect to the docker daemon",
                "connection refused",
                "could not resolve host",
                "certificate_verify_failed",
                "unauthorized",
                "manifest unknown",
                "selected git installation does not exist",
                "git executable not found",
                "agent is offline",
                "imagepullbackoff",
                "crashloopbackoff",
                "failedscheduling",
                "error acquiring the state lock",
                "quality gate failed",
                "trivy scan failed",
            )

            for marker in strong_markers:

                if marker in lower:

                    score += 30

            # Non-zero exit codes are useful, but weaker than
            # the actual underlying error.
            if re.search(
                r"\b(?:exit|status|code)"
                r"\s*[:=]?\s*[1-9]\d*\b",
                lower,
            ):

                score += 5

            # Generic Jenkins wrappers should lose priority.
            if (
                "script returned exit code"
                in lower
            ):

                score -= 25

            if (
                "process apparently never started"
                in lower
            ):

                score -= 15

            if lower.startswith(
                "[pipeline]"
            ):

                score -= 10

            # On equal scores, prefer the later occurrence.
            if score >= best_score:

                best_score = score
                best_index = index

        return best_index

    def _find_stage_for_failure(
        self,
        lines: list[str],
        failure_index: int,
    ) -> tuple[
        str | None,
        int | None,
        int,
    ]:

        markers = []

        for index, line in enumerate(
            lines
        ):

            match = (
                self.STAGE_PATTERN.match(
                    line
                )
            )

            if match:

                markers.append(
                    (
                        index,
                        match.group(
                            "stage"
                        ).strip(),
                    )
                )

        if not markers:

            return (
                None,
                None,
                len(lines),
            )

        previous = None

        for marker in markers:

            if marker[0] <= failure_index:

                previous = marker

            else:

                break

        if previous is None:

            return (
                None,
                None,
                len(lines),
            )

        stage_start = previous[0]

        stage_name = previous[1]

        stage_end = len(
            lines
        )

        # The next stage is a hard boundary.
        #
        # This prevents:
        #
        # Test failure
        # +
        # Docker stage
        #
        # from being combined into one AI context.
        for marker_index, _ in markers:

            if (
                marker_index
                > stage_start
            ):

                stage_end = (
                    marker_index
                )

                break

        return (
            stage_name,
            stage_start,
            stage_end,
        )

    def _trim_stage_around_failure(
        self,
        stage_lines: list[str],
        failure_offset: int,
    ) -> list[str]:

        context = max(
            5,
            self.failure_context_lines,
        )

        while True:

            start = max(
                0,
                failure_offset
                - context,
            )

            end = min(
                len(stage_lines),
                failure_offset
                + context
                + 1,
            )

            selected = stage_lines[
                start:end
            ]

            if (
                len(
                    "\n".join(
                        selected
                    )
                )
                <= self.max_stage_chars
            ):

                return selected

            if context <= 5:

                return selected

            context = max(
                5,
                context // 2,
            )

    def _window(
        self,
        lines: list[str],
        index: int,
        radius: int,
    ) -> str:

        start = max(
            0,
            index - radius,
        )

        end = min(
            len(lines),
            index + radius + 1,
        )

        return "\n".join(
            lines[start:end]
        )

    def _is_failure_line(
        self,
        line: str,
        rules_category: str | None = None,
    ) -> bool:

        text = line.strip()

        if not text:

            return False

        signals = (
            self.CATEGORY_SIGNALS.get(
                rules_category or "",
                self.FAILURE_SIGNALS,
            )
        )

        lower = text.lower()

        if any(
            signal.lower()
            in lower
            for signal in signals
        ):

            return True

        # ---------------------------------------------------------
        # Generic tool-independent failure patterns.
        #
        # These deliberately detect evidence without assigning
        # remediation.
        # ---------------------------------------------------------

        generic_patterns = (
            r"\b(?:fatal|panic|exception|error|failure|failed)\b",

            r"\b(?:exit|status)"
            r"\s+(?:code\s+)?[1-9]\d*\b",

            r"\breturned\s+(?:a\s+)?"
            r"non[- ]zero\b",

            r"\bnon[- ]zero\s+exit\b",

            r"\bcommand\s+failed\b",

            r"\b(?:cannot|can't|couldn't|unable to)\b"
            r".*\b(?:connect|access|create|open|find|"
            r"resolve|start|pull|push)\b",
        )

        return any(
            re.search(
                pattern,
                text,
                re.IGNORECASE,
            )
            for pattern in generic_patterns
        )

    def _count_failure_lines(
        self,
        log: str,
    ) -> int:

        return sum(
            1
            for line in log.splitlines()
            if self._is_failure_line(
                line
            )
        )

    def _redact_secrets(
        self,
        text: str,
    ) -> str:

        patterns = (
            (
                r"(?i)"
                r"(Authorization:\s*Bearer\s+)"
                r"[^\s]+",
                r"\1[REDACTED]",
            ),
            (
                r"(?i)"
                r"(Authorization:\s*Basic\s+)"
                r"[^\s]+",
                r"\1[REDACTED]",
            ),
            (
                r"(?i)"
                r"(api[_-]?key\s*[:=]\s*)"
                r"[^\s]+",
                r"\1[REDACTED]",
            ),
            (
                r"(?i)"
                r"(token\s*[:=]\s*)"
                r"[^\s]+",
                r"\1[REDACTED]",
            ),
            (
                r"(?i)"
                r"(password\s*[:=]\s*)"
                r"[^\s]+",
                r"\1[REDACTED]",
            ),
            (
                r"(?i)"
                r"(secret\s*[:=]\s*)"
                r"[^\s]+",
                r"\1[REDACTED]",
            ),
            (
                r"(?i)"
                r"(access[_-]?key\s*[:=]\s*)"
                r"[^\s]+",
                r"\1[REDACTED]",
            ),
            (
                r"(?i)"
                r"(private[_-]?key\s*[:=]\s*)"
                r"[^\s]+",
                r"\1[REDACTED]",
            ),
            (
                r"(?i)"
                r"(AWS_SECRET_ACCESS_KEY\s*=\s*)"
                r"[^\s]+",
                r"\1[REDACTED]",
            ),
            (
                r"(?i)"
                r"(AWS_SESSION_TOKEN\s*=\s*)"
                r"[^\s]+",
                r"\1[REDACTED]",
            ),
            (
                r"(?i)"
                r"(ghp_[A-Za-z0-9_\-]+)",
                r"[REDACTED_GITHUB_TOKEN]",
            ),
            (
                r"(?i)"
                r"(glpat-[A-Za-z0-9_\-]+)",
                r"[REDACTED_GITLAB_TOKEN]",
            ),
        )

        result = text

        for pattern, replacement in patterns:

            result = re.sub(
                pattern,
                replacement,
                result,
            )

        return result

    def _hard_limit(
        self,
        text: str,
    ) -> str:

        if (
            len(text)
            <= self.max_log_chars
        ):

            return text

        # Preserve both the beginning and end.
        #
        # The end often contains the actual command failure,
        # exit status or final exception.
        head = max(
            1000,
            self.max_log_chars // 3,
        )

        tail = (
            self.max_log_chars
            - head
        )

        return (
            text[:head]
            + "\n\n"
            "===== AI CONTEXT TRUNCATED =====\n"
            "Middle of targeted evidence was "
            "removed to enforce the AI input limit.\n\n"
            + text[-tail:]
        )

    def _normalise_for_compare(
        text: str,
    ) -> str:

        return re.sub(
            r"\s+",
            " ",
            text or "",
        ).strip()