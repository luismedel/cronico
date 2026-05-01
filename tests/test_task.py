import io
import logging
import subprocess
from pathlib import Path

import pytest

from cronico.main import Task, get_fresh_env, run_task


def make_task(tmp_path: Path, **overrides) -> Task:
    cfg = {
        "cron": "*/5 * * * *",
        "command": "echo hello",
        "working_dir": str(tmp_path),
    }
    cfg.update(overrides)
    return Task("example", cfg)


def attach_log_buffer(task: Task) -> io.StringIO:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    task.logger.handlers = [handler]
    return stream


def test_task_init_sets_defaults(tmp_path: Path) -> None:
    task = make_task(tmp_path)

    assert task.description is None
    assert task.retry_on_error is False
    assert task.max_attempts == 1
    assert task.timeout is None
    assert task.environment == {}
    assert task.working_dir == str(tmp_path)
    assert task.next_run is not None


def test_task_init_coerces_max_attempts_and_timeout(tmp_path: Path) -> None:
    task = make_task(tmp_path, max_attempts="3", timeout="2.5")

    assert task.max_attempts == 3
    assert task.timeout == 2.5


@pytest.mark.parametrize(
    "cfg",
    [
        {"command": "echo hello"},
        {"cron": "*/5 * * * *"},
    ],
)
def test_task_init_requires_cron_and_command(tmp_path: Path, cfg: dict) -> None:
    with pytest.raises(ValueError):
        Task("broken", cfg)


def test_get_fresh_env_merges_process_dotenv_and_task_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("FROM_FILE=file-value\nOVERRIDE=from-file\n", encoding="utf-8")

    monkeypatch.setenv("FROM_OS", "os-value")
    monkeypatch.setenv("OVERRIDE", "from-os")

    task = make_task(
        tmp_path,
        env_file=str(env_file),
        environment={"OVERRIDE": "from-task", "FROM_TASK": 123},
    )

    fresh_env = get_fresh_env(task)

    assert fresh_env["FROM_OS"] == "os-value"
    assert fresh_env["FROM_FILE"] == "file-value"
    assert fresh_env["OVERRIDE"] == "from-task"
    assert fresh_env["FROM_TASK"] == "123"


def test_run_task_executes_command_and_returns_exit_code(tmp_path: Path) -> None:
    task = make_task(tmp_path, command="printf 'hello\\n'")
    stream = attach_log_buffer(task)

    returncode = run_task(task)

    assert returncode == 0
    output = stream.getvalue()
    assert "hello" in output
    assert "Process exited with code 0" in output


def test_run_task_times_out_and_kills_process(tmp_path: Path) -> None:
    task = make_task(tmp_path, command="sleep 1", timeout=0.01)
    stream = attach_log_buffer(task)

    returncode = run_task(task)

    assert returncode != 0
    output = stream.getvalue()
    assert "Timeout after 0.01s, killing process..." in output


def test_run_task_returns_error_when_process_fails_to_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    task = make_task(tmp_path)
    stream = attach_log_buffer(task)

    def fake_popen(*args, **kwargs):
        raise OSError("boom")

    monkeypatch.setattr("cronico.main.subprocess.Popen", fake_popen)

    assert run_task(task) == 1
    assert "Failed to start process: boom" in stream.getvalue()


def test_run_task_handles_shebang_script_and_removes_tempfile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    task = make_task(
        tmp_path,
        command="#!/bin/sh\nprintf 'script-run\\n'",
    )
    stream = attach_log_buffer(task)
    removed: list[str] = []

    def fake_remove(path: str) -> None:
        removed.append(path)
        Path(path).unlink()

    monkeypatch.setattr("cronico.main.os.remove", fake_remove)

    returncode = run_task(task)

    assert returncode == 0
    assert removed
    assert not Path(removed[0]).exists()
    assert "script-run" in stream.getvalue()


def test_run_task_rejects_empty_shebang_body(tmp_path: Path) -> None:
    task = make_task(tmp_path, command="#!/bin/sh\n")
    stream = attach_log_buffer(task)

    assert run_task(task) == 1
    assert "No script body found after shebang" in stream.getvalue()


def test_task_run_logs_success_and_resets_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    task = make_task(tmp_path)
    task.mark_pending()
    stream = attach_log_buffer(task)
    original_next_run = task.next_run

    monkeypatch.setattr("cronico.main.run_task", lambda current: 0)

    task.run()

    output = stream.getvalue()
    assert "Starting task 'example'" in output
    assert "Attempt 1/1" in output
    assert "Finished successfully" in output
    assert task.is_busy is False
    assert task.last_run is not None
    assert task.next_run > task.last_run


def test_task_run_stops_after_first_failure_without_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    task = make_task(tmp_path, retry_on_error=False, max_attempts=3)
    stream = attach_log_buffer(task)
    calls: list[int] = []

    def fake_run(current: Task) -> int:
        calls.append(1)
        return 2

    monkeypatch.setattr("cronico.main.run_task", fake_run)

    task.run()

    assert len(calls) == 1
    assert "Failed (exit 2)" in stream.getvalue()


def test_task_run_retries_until_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    task = make_task(tmp_path, retry_on_error=True, max_attempts=3)
    stream = attach_log_buffer(task)
    return_codes = iter([1, 0])

    monkeypatch.setattr("cronico.main.run_task", lambda current: next(return_codes))

    task.run()

    output = stream.getvalue()
    assert "Attempt 1/3" in output
    assert "Attempt 2/3" in output
    assert "Finished successfully" in output


def test_task_run_retries_until_exhausted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    task = make_task(tmp_path, retry_on_error=True, max_attempts=2)
    stream = attach_log_buffer(task)
    calls: list[int] = []

    def fake_run(current: Task) -> int:
        calls.append(1)
        return 9

    monkeypatch.setattr("cronico.main.run_task", fake_run)

    task.run()

    assert len(calls) == 2
    assert stream.getvalue().count("Failed (exit 9)") == 2


def test_task_run_creates_log_file_with_expanded_variables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_dir = tmp_path / "logs"
    task = make_task(
        tmp_path,
        log_file=str(log_dir / "$NAME-$TASKID.log"),
        log_file_format_string="%(message)s",
    )

    monkeypatch.setattr("cronico.main.run_task", lambda current: 0)

    task.run()

    log_files = list(log_dir.glob("example-*.log"))
    assert len(log_files) == 1
    contents = log_files[0].read_text(encoding="utf-8")
    assert "Starting task 'example'" in contents
    assert "Finished successfully" in contents
    assert all(not getattr(handler, "baseFilename", "").endswith(log_files[0].name) for handler in task.logger.handlers)


def test_task_mark_pending_rejects_running_task(tmp_path: Path) -> None:
    task = make_task(tmp_path)
    task._running = True

    with pytest.raises(RuntimeError, match="Cannot mark a running task as pending"):
        task.mark_pending()


def test_task_run_logs_exceptions_from_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    task = make_task(tmp_path, retry_on_error=False)
    stream = attach_log_buffer(task)

    def fake_run(current: Task) -> int:
        raise RuntimeError("bad runner")

    monkeypatch.setattr("cronico.main.run_task", fake_run)

    task.run()

    assert "Exception: bad runner" in stream.getvalue()


def test_task_run_logs_timeout_expired_from_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    task = make_task(tmp_path, retry_on_error=False)
    stream = attach_log_buffer(task)

    def fake_run(current: Task) -> int:
        raise subprocess.TimeoutExpired(cmd="sleep", timeout=1)

    monkeypatch.setattr("cronico.main.run_task", fake_run)

    task.run()

    assert "Timeout expired" in stream.getvalue()


def test_run_task_logs_error_when_monitoring_loop_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    task = make_task(tmp_path)
    stream = attach_log_buffer(task)

    class FakeStream:
        def readline(self) -> str:
            return ""

    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = FakeStream()
            self.stderr = FakeStream()
            self.returncode = 99
            self.killed = False

        def poll(self):
            return None

        def kill(self) -> None:
            self.killed = True

        def wait(self) -> int:
            return self.returncode

    process = FakeProcess()

    monkeypatch.setattr("cronico.main.subprocess.Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr("cronico.main.time.sleep", lambda _: (_ for _ in ()).throw(RuntimeError("loop failed")))

    assert run_task(task) == 99
    output = stream.getvalue()
    assert "Error while running task: loop failed" in output
    assert process.killed is True


def test_run_task_warns_when_temporary_script_was_already_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = make_task(tmp_path, command="#!/bin/sh\nprintf 'script-run\\n'")
    stream = attach_log_buffer(task)

    def fake_remove(path: str) -> None:
        raise FileNotFoundError(path)

    monkeypatch.setattr("cronico.main.os.remove", fake_remove)

    assert run_task(task) == 0
    assert "Temporary script file" in stream.getvalue()
