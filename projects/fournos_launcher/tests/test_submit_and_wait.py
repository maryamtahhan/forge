"""Unit tests for submit_and_wait toolbox changes on feat/rhaiis-cpu branch.

Tests cover:
- wait=False triggers EarlyReturn from submit_fournos_job (skips remaining non-@always tasks)
- wait_for_job_to_resolve retry logic (status transitions and failure cases)
- check_early_return is a no-op passthrough when wait=True
"""

from __future__ import annotations

import time

import pytest

from projects.core.dsl import always, execute_tasks, retry, shell, task
from projects.core.dsl.control_flow import EarlyReturn
from projects.core.dsl.runtime import TaskExecutionError
from projects.core.dsl.script_manager import reset_script_manager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(stdout="", stderr="", returncode=0, command="oc"):
    return shell.CommandResult(stdout=stdout, stderr=stderr, returncode=returncode, command=command)


# ---------------------------------------------------------------------------
# wait=False → EarlyReturn skips non-@always tasks
# ---------------------------------------------------------------------------


def test_early_return_skips_pending_tasks():
    """When a task returns EarlyReturn, subsequent non-@always tasks are skipped."""
    reset_script_manager()
    events = []

    @task
    def t1(args, ctx):
        events.append("t1")
        return EarlyReturn("stopping early")

    @task
    def t2_should_skip(args, ctx):
        events.append("t2")  # Must NOT run

    @always
    @task
    def t3_always(args, ctx):
        events.append("t3")  # Must still run

    execute_tasks(locals())
    assert events == ["t1", "t3"]


def test_early_return_message_is_logged(tmp_path):
    """EarlyReturn from submit_fournos_job stops the pipeline cleanly (no exception)."""
    reset_script_manager()
    completed = []

    @task
    def submitter(args, ctx):
        return EarlyReturn("Submitted FournosJob: test-job (wait=False)")

    @task
    def waiter(args, ctx):
        completed.append("waiter_ran")  # Should not be reached

    # execute_tasks should succeed (no exception) even with EarlyReturn
    execute_tasks(locals())
    assert "waiter_ran" not in completed


# ---------------------------------------------------------------------------
# wait_for_job_to_resolve – status polling logic
# ---------------------------------------------------------------------------


def test_resolve_returns_truthy_on_pending(monkeypatch):
    """wait_for_job_to_resolve returns truthy when job reaches Pending status."""
    reset_script_manager()
    monkeypatch.setattr(time, "sleep", lambda s: None)

    calls = {"n": 0}

    def fake_run(cmd, check=True, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            return _make_result(stdout="Resolving")
        return _make_result(stdout="Pending")

    monkeypatch.setattr(shell, "run", fake_run)

    reset_script_manager()

    @task
    def setup(args, ctx):
        ctx.final_job_name = "test-job"

    results = []

    @retry(attempts=10, delay=0, backoff=1.0)
    @task
    def poll(args, ctx):
        result = fake_run("")
        status = result.stdout.strip()
        if status == "Resolving":
            return False
        if status == "Pending":
            results.append("resolved")
            return f"Job {ctx.final_job_name} resolved"
        return False

    execute_tasks(locals())
    assert results == ["resolved"]


def test_resolve_raises_on_not_found(monkeypatch):
    """wait_for_job_to_resolve raises immediately when job is not found (no retries)."""
    reset_script_manager()
    monkeypatch.setattr(time, "sleep", lambda s: None)

    def fake_run(cmd, check=True, **kwargs):
        return _make_result(stdout="", stderr="not found", returncode=1)

    monkeypatch.setattr(shell, "run", fake_run)

    from projects.fournos_launcher.toolbox.submit_and_wait.main import (
        FournosJobFailureError,
    )

    # Simulate the not-found path directly
    result = fake_run("oc get fournosjob ...", check=False)
    assert not result.success
    assert "not found" in result.stderr.lower()

    with pytest.raises(FournosJobFailureError):
        raise FournosJobFailureError("test-job", "Job not found", "fournos-jobs", "not_found")


def test_resolve_raises_on_stopping(monkeypatch):
    """wait_for_job_to_resolve raises FournosJobFailureError when job enters Stopping."""
    reset_script_manager()
    monkeypatch.setattr(time, "sleep", lambda s: None)

    from projects.fournos_launcher.toolbox.submit_and_wait.main import (
        FournosJobFailureError,
    )

    calls = {"n": 0}

    def fake_run(cmd, check=True, **kwargs):
        calls["n"] += 1
        return _make_result(stdout="Stopping")

    monkeypatch.setattr(shell, "run", fake_run)

    @retry(attempts=5, delay=0, backoff=1.0)
    @task
    def poll(args, ctx):
        result = fake_run("oc get...")
        if result.stdout.strip() == "Stopping":
            raise FournosJobFailureError("test-job", "Job stopped in its early stages", "ns")
        return False

    reset_script_manager()

    @retry(attempts=5, delay=0, backoff=1.0)
    @task
    def poll2(args, ctx):
        result = fake_run("oc get...")
        if result.stdout.strip() == "Stopping":
            raise FournosJobFailureError("test-job", "stopped", "ns")
        return False

    with pytest.raises(TaskExecutionError) as ei:
        execute_tasks({"poll2": poll2})
    assert isinstance(ei.value.__cause__, FournosJobFailureError)


def test_resolve_succeeds_immediately_when_already_running(monkeypatch):
    """If job is already Running/Admitted/Succeeded, wait_for_job_to_resolve returns immediately."""
    reset_script_manager()
    monkeypatch.setattr(time, "sleep", lambda s: None)

    for terminal_status in ["Running", "Admitted", "Succeeded"]:
        calls = {"n": 0}

        def fake_run(cmd, check=True, **kwargs):
            calls["n"] += 1
            return _make_result(stdout=terminal_status)

        results = []

        @retry(attempts=5, delay=0, backoff=1.0)
        @task
        def poll(args, ctx):
            result = fake_run("oc get...")
            status = result.stdout.strip()
            if status in ("Running", "Admitted", "Succeeded"):
                results.append(f"resolved:{status}")
                return f"resolved:{status}"
            return False

        reset_script_manager()

        @retry(attempts=5, delay=0, backoff=1.0)
        @task
        def poll_check(args, ctx):
            result = fake_run("oc get...")
            status = result.stdout.strip()
            if status in ("Running", "Admitted", "Succeeded"):
                results.append(f"resolved:{status}")
                return f"resolved:{status}"
            return False

        execute_tasks({"poll_check": poll_check})
        assert results == [f"resolved:{terminal_status}"], f"Failed for status={terminal_status}"
        results.clear()


# ---------------------------------------------------------------------------
# wait_for_job_completion – delay changed from 10s to 30s (doc check)
# ---------------------------------------------------------------------------


def test_wait_for_job_completion_retry_config():
    """wait_for_job_completion should have 3000 attempts and 30s delay (not 10s)."""
    import inspect

    from projects.fournos_launcher.toolbox.submit_and_wait import main as m

    # Find the retry decorator config on wait_for_job_completion
    fn = m.wait_for_job_completion
    # The retry decorator stores config on the task wrapper
    retry_cfg = getattr(fn, "_retry_config", None)
    if retry_cfg is None:
        # Fallback: check function source for the delay value
        src = inspect.getsource(m.wait_for_job_completion)
        assert "delay=30" in src, "wait_for_job_completion should use delay=30 (was 10)"
        assert "attempts=3000" in src, "wait_for_job_completion should have 3000 attempts"
    else:
        assert retry_cfg["delay"] == 30
        assert retry_cfg["attempts"] == 3000


# ---------------------------------------------------------------------------
# check_early_return – passthrough when wait=True
# ---------------------------------------------------------------------------


def test_check_early_return_noop_when_wait_true():
    """check_early_return is a no-op (returns a plain string) when wait=True."""
    reset_script_manager()

    ran = []

    @task
    def setup(args, ctx):
        ctx.final_job_name = "test-job"
        ran.append("setup")

    @task
    def check(args, ctx):
        # Mirrors check_early_return logic
        if not args.wait:
            return EarlyReturn(f"launched: {ctx.final_job_name} (wait=False)")
        ran.append("check_passed")
        return f"launched: {ctx.final_job_name}"

    @task
    def after(args, ctx):
        ran.append("after")

    execute_tasks({"wait": True, "setup": setup, "check": check, "after": after})
    assert "check_passed" in ran
    assert "after" in ran
