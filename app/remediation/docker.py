class DockerRemediator:
    """Create a Jenkins-side, scoped Docker cache-bypass recovery plan.

    The Shared Library checks Docker availability and sets
    BUILDSENTRY_DOCKER_NO_CACHE=true. The Jenkinsfile must consume that flag
    in its own docker build command. BuildSentry never prunes the host-wide
    cache or restarts the Docker daemon.
    """

    async def remediate(self, job_name: str) -> dict:
        return {
            "action": "DOCKER_CACHE_BYPASS_AND_RETRY",
            "success": True,
            "remediation_performed": False,
            "parameters": {
                "AUTOHEAL_ACTION": "DOCKER_CACHE_BYPASS",
            },
            "message": (
                "Docker recovery was requested. The Jenkins Shared Library "
                "must pass its daemon preflight and the Jenkinsfile must use "
                "BUILDSENTRY_DOCKER_NO_CACHE for a scoped no-cache build."
            ),
        }