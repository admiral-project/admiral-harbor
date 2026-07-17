# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from worker import _run_worker_step


def test_worker_step_failure_is_reported_without_raising(caplog):
    def failed_step():
        raise RuntimeError("database unavailable")

    with caplog.at_level("ERROR", logger="worker"):
        result = _run_worker_step("reconcile_operations", failed_step)

    assert result == (0, 1)
    assert "Worker step failed: reconcile_operations" in caplog.text


def test_worker_step_returns_actions_and_errors():
    assert _run_worker_step("sync_remote_instances", lambda: (3, 2)) == (3, 2)
