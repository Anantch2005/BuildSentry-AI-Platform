
import asyncio
from urllib.parse import quote, urljoin

import httpx

from app.config import settings


class JenkinsRemediator:
    def __init__(self):
        self.base_url = settings.jenkins_url.rstrip("/")
        self.auth = (
            settings.jenkins_username,
            settings.jenkins_api_token,
        )

    def _job_url(self, job_name: str) -> str:
        """Convert folder/job/branch into Jenkins /job/... segments."""
        parts = [
            part.strip()
            for part in job_name.strip("/").split("/")
            if part.strip()
        ]
        if not parts:
            raise ValueError("Jenkins job name cannot be empty.")

        path = "".join(
            f"/job/{quote(part, safe='')}"
            for part in parts
        )
        return f"{self.base_url}{path}"

    async def trigger_build(
        self,
        job_name: str,
        parameters: dict | None = None,
    ) -> dict:
        job_url = self._job_url(job_name)

        if parameters:
            url = f"{job_url}/buildWithParameters"
        else:
            url = f"{job_url}/build"

        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                auth=self.auth,
                data=parameters,
                timeout=30,
            )
            response.raise_for_status()

            location = response.headers.get("Location")
            if not location:
                raise RuntimeError(
                    "Jenkins did not return a queue URL."
                )

            queue_url = urljoin(
                f"{self.base_url}/",
                location,
            )

        build_number = await self.wait_for_queue(queue_url)

        return {
            "success": True,
            "message": "Jenkins remediation build triggered.",
            "build_number": build_number,
            "queue_url": queue_url,
        }

    async def wait_for_queue(
        self,
        queue_url: str,
        timeout: int = 120,
    ) -> int:
        api_url = f"{queue_url.rstrip('/')}/api/json"
        elapsed = 0

        async with httpx.AsyncClient() as client:
            while elapsed < timeout:
                response = await client.get(
                    api_url,
                    auth=self.auth,
                    timeout=20,
                )
                response.raise_for_status()
                data = response.json()

                if data.get("cancelled"):
                    raise RuntimeError(
                        "Jenkins queue item was cancelled."
                    )

                executable = data.get("executable")
                if executable and executable.get("number") is not None:
                    return int(executable["number"])

                await asyncio.sleep(2)
                elapsed += 2

        raise TimeoutError(
            "Timed out waiting for Jenkins queue item."
        )

    async def get_build_details(
        self,
        job_name: str,
        build_number: int,
        timeout: int = 600,
    ) -> dict:
        """Wait for a build to finish and return its result and description."""
        job_url = self._job_url(job_name)
        url = f"{job_url}/{int(build_number)}/api/json?tree=number,result,building,description"
        elapsed = 0

        async with httpx.AsyncClient() as client:
            while elapsed < timeout:
                response = await client.get(url, auth=self.auth, timeout=20)
                response.raise_for_status()
                data = response.json()
                if not data.get("building", False):
                    return {
                        "build_number": data.get("number", int(build_number)),
                        "result": data.get("result") or "UNKNOWN",
                        "description": data.get("description") or "",
                    }
                await asyncio.sleep(5)
                elapsed += 5

        raise TimeoutError(f"Build #{build_number} did not finish within the timeout.")

    async def get_build_result(
        self,
        job_name: str,
        build_number: int,
        timeout: int = 600,
    ) -> str:
        """Backward-compatible result-only helper."""
        details = await self.get_build_details(job_name, build_number, timeout)
        return details["result"]