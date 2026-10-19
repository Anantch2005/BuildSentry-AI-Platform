

import groovy.json.JsonOutput

/**
 * BuildSentry Jenkins Shared Library.
 *
 * The backend selects an allowlisted action.
 * This library executes that action on the Jenkins agent.
 *
 * Supported hooks:
 *   buildsentry(stage: 'checkout')
 *   buildsentry(stage: 'test')
 *   buildsentry(stage: 'docker')
 *   buildsentry(stage: 'registry')
 *
 * Normal failures are reported from post { failure { ... } }.
 */

def call(Map config = [:]) {
    String action = (env.AUTOHEAL_ACTION ?: '').trim()
    String stageName = (config.stage ?: '')
        .toString()
        .trim()
        .toLowerCase()

    if (!action || action == 'NONE') {
        if (currentBuild.currentResult == 'FAILURE') {
            try {
                sendFailureWebhook(config)
            } catch (Exception ex) {
                echo "BuildSentry: webhook notification failed; preserving original build failure: ${ex.message}"
            }
        } else {
            echo 'BuildSentry: no recovery action requested.'
        }
        return
    }

    env.AUTOHEAL_RETRY = 'true'

    echo """
========================================
          BuildSentry Recovery
========================================
Action : ${action}
Build  : ${env.BUILD_NUMBER}
Stage  : ${stageName ?: 'unspecified'}
========================================
"""

    String requiredStage = requiredStageFor(action)

    if (requiredStage == 'unsupported') {
        error("BuildSentry: unsupported action '${action}'. Refusing execution.")
    }

    if (stageName != requiredStage) {
        echo "BuildSentry: action '${action}' is waiting for stage '${requiredStage}'."
        return
    }

    if ((env.AUTOHEAL_ACTION_APPLIED ?: '') == 'true') {
        echo 'BuildSentry: action already executed in this build; skipping duplicate hook.'
        return
    }

    // Set the guard before executing the action. A failed action must
    // not be accidentally executed again by another hook in this build.
    env.AUTOHEAL_ACTION_APPLIED = 'true'

    applyBuildSentryAction(action)

    // The backend requires this Jenkins API-visible marker plus a SUCCESS result.
    String marker = "BuildSentry-Recovery:${action}:APPLIED"
    String existingDescription = currentBuild.description ?: ''
    if (!existingDescription.contains(marker)) {
        currentBuild.description = existingDescription
            ? "${existingDescription} | ${marker}"
            : marker
    }

    echo "BuildSentry: action '${action}' completed and execution marker recorded."
}

private String requiredStageFor(String action) {
    switch (action) {
        case 'CLEAN_WORKSPACE':
        case 'CLEAN_WORKSPACE_AND_RETRY':
        case 'RETRY':
        case 'RETRY_FLAKY_TEST':
            return 'checkout'

        case 'CLEAN_DEPENDENCY_ENV':
        case 'CLEAN_DEPENDENCY_ENV_AND_RETRY':
        case 'CONNECTIVITY_CHECK_BACKOFF':
        case 'CONNECTIVITY_CHECK_BACKOFF_AND_RETRY':
            return 'test'

        case 'DOCKER_CACHE_BYPASS':
        case 'DOCKER_CACHE_BYPASS_AND_RETRY':
        case 'INVALIDATE_DOCKER_CACHE': // backward compatibility
        case 'INVALIDATE_DOCKER_CACHE_AND_RETRY':
            return 'docker'

        case 'RETRY_REGISTRY':
            return 'registry'

        default:
            return 'unsupported'
    }
}

private void applyBuildSentryAction(String action) {
    switch (action) {
        case 'RETRY':
        case 'RETRY_FLAKY_TEST':
        case 'RETRY_REGISTRY':
            echo "BuildSentry: approved controlled retry requested (${action})."
            break

        case 'CLEAN_WORKSPACE':
        case 'CLEAN_WORKSPACE_AND_RETRY':
            echo 'BuildSentry: deleting the current workspace before checkout.'
            deleteDir()
            break

        case 'CLEAN_DEPENDENCY_ENV':
        case 'CLEAN_DEPENDENCY_ENV_AND_RETRY':
            remediateDependencies()
            break

        case 'DOCKER_CACHE_BYPASS':
        case 'DOCKER_CACHE_BYPASS_AND_RETRY':
        case 'INVALIDATE_DOCKER_CACHE': // backward compatibility
        case 'INVALIDATE_DOCKER_CACHE_AND_RETRY':
            remediateDockerCache()
            break

        case 'CONNECTIVITY_CHECK_BACKOFF':
        case 'CONNECTIVITY_CHECK_BACKOFF_AND_RETRY':
            remediateNetworkTransient()
            break

        default:
            error("BuildSentry: action '${action}' is not allowlisted.")
    }
}

/**
 * Recreate a supported dependency environment from an existing manifest.
 * Does not change lockfiles or upgrade project dependency versions.
 */
private void remediateDependencies() {
    sh(
        label: 'BuildSentry dependency recovery',
        script: '''
            set -eu

            if [ -f requirements.txt ]; then
                command -v python >/dev/null 2>&1 || {
                    echo "Python is unavailable." >&2
                    exit 20
                }

                rm -rf .venv
                python -m venv .venv

                .venv/bin/python -m pip install \
                    --disable-pip-version-check \
                    -r requirements.txt

                .venv/bin/python -c \
                    "import sys; print('Recovered Python:', sys.version)"

            elif [ -f package.json ] && [ -f package-lock.json ]; then
                command -v npm >/dev/null 2>&1 || {
                    echo "npm is unavailable." >&2
                    exit 21
                }

                rm -rf node_modules
                npm ci --no-audit --no-fund

            elif [ -f package.json ] && [ -f yarn.lock ]; then
                command -v yarn >/dev/null 2>&1 || {
                    echo "yarn is unavailable." >&2
                    exit 22
                }

                rm -rf node_modules
                yarn install --frozen-lockfile

            elif [ -f package.json ] && [ -f pnpm-lock.yaml ]; then
                command -v pnpm >/dev/null 2>&1 || {
                    echo "pnpm is unavailable." >&2
                    exit 23
                }

                rm -rf node_modules
                pnpm install --frozen-lockfile

            else
                echo "No supported dependency manifest found." >&2
                exit 24
            fi
        '''
    )

    if (fileExists('requirements.txt')) {
        env.PATH = "${pwd()}/.venv/bin:${env.PATH}"
    }

    echo 'BuildSentry: dependency recovery commands completed.'
}

/**
 * Verify Docker is reachable. The recovery build itself uses --no-cache in
 * the Calculator Jenkinsfile, avoiding a broad daemon-wide cache prune that
 * could affect unrelated jobs sharing this Docker host.
 */
private void remediateDockerCache() {
    sh(
        label: 'BuildSentry Docker cache recovery preflight',
        script: '''
            set -eu

            command -v docker >/dev/null 2>&1 || {
                echo "Docker CLI unavailable." >&2
                exit 30
            }

            docker info >/dev/null 2>&1 || {
                echo "Docker daemon unavailable; escalating." >&2
                exit 31
            }

            echo "Docker daemon is available; Jenkinsfile will run the recovery build with --no-cache."
        '''
    )

    env.BUILDSENTRY_DOCKER_NO_CACHE = 'true'
    echo 'BuildSentry: Docker preflight passed; Jenkinsfile must consume BUILDSENTRY_DOCKER_NO_CACHE for its scoped build.'
}

/**
 * Perform bounded outbound connectivity checks.
 * Does not change DNS settings, bypass authentication, or switch registries.
 */
private void remediateNetworkTransient() {
    sh(
        label: 'BuildSentry bounded connectivity check',
        script: '''
            set -eu

            command -v curl >/dev/null 2>&1 || {
                echo "curl is unavailable." >&2
                exit 40
            }

            for delay in 2 4 8; do
                echo "BuildSentry: waiting ${delay}s before connectivity check."
                sleep "$delay"

                if curl \
                    --fail \
                    --silent \
                    --show-error \
                    --max-time 10 \
                    https://github.com >/dev/null; then
                    echo "BuildSentry: outbound HTTPS connectivity is available."
                    exit 0
                fi
            done

            echo "BuildSentry: connectivity remains unavailable after bounded checks." >&2
            exit 41
        '''
    )
}

/**
 * Notify the backend about a failed build.
 * Notification errors must not hide the original pipeline failure.
 */
private void sendFailureWebhook(Map config = [:]) {
    String baseUrl = (
        config.url ?: env.AUTOHEAL_URL ?: 'http://127.0.0.1:8000'
    ).replaceAll('/+$', '')

    String credentialId = (
        config.secretCredentialId ?: 'autoheal-webhook-secret'
    )

    String buildNumber = (env.BUILD_NUMBER ?: '0').toString()



    Integer autoHealBuildNumber = currentBuild.number as Integer

    if (autoHealBuildNumber == null || autoHealBuildNumber < 1) {
        echo 'BuildSentry: cannot determine a valid Jenkins build number; skipping webhook.'
        return
    }

    String payload = groovy.json.JsonOutput.toJson([
        job_name: env.JOB_NAME ?: '',
        build_number: autoHealBuildNumber,
        build_url: env.BUILD_URL ?: '',
        status: 'FAILURE'
   ])


    withCredentials([
        string(
            credentialsId: credentialId,
            variable: 'AUTOHEAL_WEBHOOK_SECRET'
        )
    ]) {
        withEnv([
            "AUTOHEAL_PAYLOAD=${payload}",
            "AUTOHEAL_WEBHOOK_URL=${baseUrl}/webhook/jenkins"
        ]) {
            sh(
                label: 'Notify BuildSentry backend',
                script: '''
                    set -eu

                    response_file="$(mktemp)"
                    trap 'rm -f "$response_file"' EXIT

                    status_code=$(curl \
                        --silent \
                        --show-error \
                        --output "$response_file" \
                        --write-out '%{http_code}' \
                        --connect-timeout 5 \
                        --max-time 15 \
                        --request POST \
                        --header 'Content-Type: application/json' \
                        --header "X-BuildSentry-Secret: ${AUTOHEAL_WEBHOOK_SECRET}" \
                        --data "$AUTOHEAL_PAYLOAD" \
                        "$AUTOHEAL_WEBHOOK_URL" || true)

                    case "$status_code" in
                        200|202)
                            echo "BuildSentry webhook accepted."
                            ;;
                        *)
                            echo "BuildSentry webhook returned HTTP status: ${status_code}"
                            if [ -s "$response_file" ]; then
                                cat "$response_file"
                            fi
                            # Do not overwrite the original pipeline failure.
                            ;;
                    esac
                '''
            )
        }
    }
}