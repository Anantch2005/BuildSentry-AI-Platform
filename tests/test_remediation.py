
import pytest

from app.remediation.executor import RemediationExecutor


class FakeJenkins:

    def __init__(self, result="SUCCESS", action_applied=True):
        self.triggered = False
        self.parameters = None
        self.result = result
        self.action_applied = action_applied

    async def trigger_build(
        self,
        job_name,
        parameters=None,
    ):

        self.triggered = True
        self.parameters = parameters

        return {
            "success": True,
            "build_number": 200,
            "queue_url": "fake-queue",
        }

    async def get_build_details(self, job_name, build_number):
        action = (self.parameters or {}).get("AUTOHEAL_ACTION", "")
        marker = f"BuildSentry-Recovery:{action}:APPLIED" if self.action_applied else ""
        return {"build_number": build_number, "result": self.result, "description": marker}

    async def get_build_result(self, job_name, build_number):
        return self.result


@pytest.mark.asyncio
async def test_flaky_test_is_healed():

    executor = RemediationExecutor()

    fake = FakeJenkins(
        result="SUCCESS"
    )

    executor.jenkins = fake

    result = await executor.execute(
        job_name="prac",
        category="FLAKY_TEST",
        action="RETRY",
    )

    assert fake.triggered is True

    assert fake.parameters == {
        "AUTOHEAL_ACTION": "RETRY",
    }

    assert result["success"] is True
    assert result["action"] == "RETRY"
    assert result["new_build_number"] == 200
    assert (
        result["verification_result"]
        == "SUCCESS"
    )


@pytest.mark.asyncio
async def test_failed_retry_escalates():

    executor = RemediationExecutor()

    fake = FakeJenkins(
        result="FAILURE"
    )

    executor.jenkins = fake

    result = await executor.execute(
        job_name="prac",
        category="FLAKY_TEST",
        action="RETRY",
    )

    assert fake.triggered is True

    assert result["success"] is False
    assert result["action"] == "ESCALATE"
    assert result["new_build_number"] == 200
    assert (
        result["verification_result"]
        == "FAILURE"
    )


@pytest.mark.asyncio
async def test_workspace_failure_cleans_workspace_and_fresh_checkout():

    executor = RemediationExecutor()

    fake = FakeJenkins(
        result="SUCCESS"
    )

    executor.jenkins = fake

    result = await executor.execute(
        job_name="prac",
        category="WORKSPACE_FAILURE",
        action="CLEAN_WORKSPACE_AND_RETRY",
    )

    assert fake.triggered is True

    assert fake.parameters == {
        "AUTOHEAL_ACTION": "CLEAN_WORKSPACE",
    }

    assert result["success"] is True
    assert (
        result["action"]
        == "CLEAN_WORKSPACE_AND_RETRY"
    )
    assert (
        result["verification_result"]
        == "SUCCESS"
    )


@pytest.mark.asyncio
async def test_dependency_failure_rebuilds_dependency_environment():

    executor = RemediationExecutor()

    fake = FakeJenkins(
        result="SUCCESS"
    )

    executor.jenkins = fake

    result = await executor.execute(
        job_name="prac",
        category="DEPENDENCY_FAILURE",
        action=(
            "CLEAN_DEPENDENCY_ENV_AND_RETRY"
        ),
    )

    assert fake.triggered is True

    assert fake.parameters == {
        "AUTOHEAL_ACTION": "CLEAN_DEPENDENCY_ENV",
    }

    assert result["success"] is True

    assert (
        result["action"]
        == "CLEAN_DEPENDENCY_ENV_AND_RETRY"
    )

    assert (
        result["verification_result"]
        == "SUCCESS"
    )


@pytest.mark.asyncio
async def test_docker_failure_bypasses_cache_before_retry():

    executor = RemediationExecutor()

    fake = FakeJenkins(
        result="SUCCESS"
    )

    executor.jenkins = fake

    result = await executor.execute(
        job_name="prac",
        category="DOCKER_FAILURE",
        action="DOCKER_CACHE_BYPASS_AND_RETRY",
    )

    assert fake.triggered is True

    assert fake.parameters == {
        "AUTOHEAL_ACTION": "DOCKER_CACHE_BYPASS",
    }

    assert result["success"] is True

    assert (
        result["action"]
        == "DOCKER_CACHE_BYPASS_AND_RETRY"
    )

    assert (
        result["verification_result"]
        == "SUCCESS"
    )


@pytest.mark.asyncio
async def test_network_failure_checks_connectivity_and_backoff(
    monkeypatch,
):

    executor = RemediationExecutor()

    fake = FakeJenkins(
        result="SUCCESS"
    )

    executor.jenkins = fake

    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(
        "app.remediation.executor.asyncio.sleep",
        fake_sleep,
    )

    result = await executor.execute(
        job_name="prac",
        category="NETWORK_FAILURE",
        action=(
            "CONNECTIVITY_CHECK_BACKOFF_AND_RETRY"
        ),
    )

    assert slept == [10]

    assert fake.triggered is True

    assert fake.parameters == {
        "AUTOHEAL_ACTION": "CONNECTIVITY_CHECK_BACKOFF",
    }

    assert result["success"] is True

    assert (
        result["action"]
        == "CONNECTIVITY_CHECK_BACKOFF_AND_RETRY"
    )

    assert (
        result["verification_result"]
        == "SUCCESS"
    )


@pytest.mark.asyncio
async def test_code_failure_is_never_retried():

    executor = RemediationExecutor()

    fake = FakeJenkins()

    executor.jenkins = fake

    result = await executor.execute(
        job_name="prac",
        category="CODE_FAILURE",
        action="DO_NOT_HEAL",
    )

    assert fake.triggered is False

    assert result["success"] is False
    assert result["action"] == "DO_NOT_HEAL"

def test_jenkins_job_url_encodes_folder_and_job_segments():
    executor = RemediationExecutor()
    assert executor.jenkins._job_url("Folder Name/job #1/feature") == (
        f"{executor.jenkins.base_url}/job/Folder%20Name/job/job%20%231/job/feature"
    )



@pytest.mark.asyncio
async def test_successful_retry_without_action_marker_escalates():
    executor = RemediationExecutor()
    fake = FakeJenkins(result="SUCCESS", action_applied=False)
    executor.jenkins = fake

    result = await executor.execute(
        job_name="prac",
        category="DEPENDENCY_FAILURE",
        action="CLEAN_DEPENDENCY_ENV_AND_RETRY",
    )

    assert result["success"] is False
    assert result["action"] == "ESCALATE"
    assert result["verification_result"] == "ACTION_NOT_CONFIRMED"
    assert result["action_verified"] is False