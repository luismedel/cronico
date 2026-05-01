import atexit
import signal
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from cronico.main import check_lockfile, cmd_daemon, remove_lockfile


def test_check_lockfile_creates_pidfile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pidfile = tmp_path / "cronico.pid"
    monkeypatch.setattr("cronico.main.os.getpid", lambda: 4321)

    check_lockfile(str(pidfile))

    assert pidfile.read_text(encoding="utf-8") == "4321"


def test_check_lockfile_removes_corrupt_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pidfile = tmp_path / "cronico.pid"
    pidfile.write_text("oops", encoding="utf-8")
    monkeypatch.setattr("cronico.main.os.getpid", lambda: 9999)

    check_lockfile(str(pidfile))

    assert pidfile.read_text(encoding="utf-8") == "9999"


def test_check_lockfile_removes_stale_pid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pidfile = tmp_path / "cronico.pid"
    pidfile.write_text("1234", encoding="utf-8")
    monkeypatch.setattr("cronico.main.os.kill", lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError()))
    monkeypatch.setattr("cronico.main.os.getpid", lambda: 5678)

    check_lockfile(str(pidfile))

    assert pidfile.read_text(encoding="utf-8") == "5678"


def test_check_lockfile_exits_when_existing_process_is_alive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pidfile = tmp_path / "cronico.pid"
    pidfile.write_text("1234", encoding="utf-8")
    monkeypatch.setattr("cronico.main.os.kill", lambda pid, sig: None)

    with pytest.raises(SystemExit, match="1"):
        check_lockfile(str(pidfile))


def test_remove_lockfile_deletes_file(tmp_path: Path) -> None:
    pidfile = tmp_path / "cronico.pid"
    pidfile.write_text("1234", encoding="utf-8")

    remove_lockfile(str(pidfile))

    assert not pidfile.exists()


def test_remove_lockfile_tolerates_remove_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pidfile = tmp_path / "cronico.pid"
    pidfile.write_text("1234", encoding="utf-8")

    monkeypatch.setattr("cronico.main.os.remove", lambda path: (_ for _ in ()).throw(OSError("blocked")))

    remove_lockfile(str(pidfile))

    assert "Could not remove lockfile: blocked" in capsys.readouterr().err


class FakeTask:
    def __init__(self, name: str, next_run: datetime, busy: bool = False) -> None:
        self.name = name
        self.next_run = next_run
        self._busy = busy
        self.pending_calls = 0
        self.run_calls = 0

    @property
    def is_busy(self) -> bool:
        return self._busy

    def mark_pending(self) -> None:
        self.pending_calls += 1
        self._busy = True

    def run(self) -> None:
        self.run_calls += 1


class FakeObserver:
    instances = []

    def __init__(self) -> None:
        self.scheduled = []
        self.started = False
        self.stopped = False
        self.joined = False
        FakeObserver.instances.append(self)

    def schedule(self, handler, path, recursive):
        self.scheduled.append((handler, path, recursive))

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def join(self) -> None:
        self.joined = True


class FakeExecutor:
    instances = []

    def __init__(self, max_workers: int) -> None:
        self.max_workers = max_workers
        self.submitted = []
        self.shutdown_wait = None
        FakeExecutor.instances.append(self)

    def submit(self, fn):
        self.submitted.append(fn)
        return SimpleNamespace()

    def shutdown(self, wait: bool) -> None:
        self.shutdown_wait = wait


class FakeStopEvent:
    def __init__(self, should_stop_after_wait: bool = True) -> None:
        self.should_stop_after_wait = should_stop_after_wait
        self.flag = False
        self.wait_calls = []

    def wait(self, timeout: float) -> None:
        self.wait_calls.append(timeout)
        if self.should_stop_after_wait:
            self.flag = True

    def is_set(self) -> bool:
        return self.flag

    def set(self) -> None:
        self.flag = True


def install_daemon_doubles(
    monkeypatch: pytest.MonkeyPatch,
    tasks_versions: list[list[FakeTask]],
    stop_event: FakeStopEvent,
) -> dict[str, object]:
    load_calls: list[str] = []
    signal_handlers: dict[int, object] = {}
    registered_atexit = []
    time_points = [datetime(2026, 1, 1, 12, 0, 0)] * 10

    FakeObserver.instances.clear()
    FakeExecutor.instances.clear()

    def fake_load_tasks(tasks_file: str):
        load_calls.append(tasks_file)
        index = min(len(load_calls) - 1, len(tasks_versions) - 1)
        return tasks_versions[index]

    monkeypatch.setattr("cronico.main.check_lockfile", lambda path: None)
    monkeypatch.setattr("cronico.main.remove_lockfile", lambda path: None)
    monkeypatch.setattr("cronico.main.load_tasks", fake_load_tasks)
    monkeypatch.setattr("cronico.main.Observer", FakeObserver)
    monkeypatch.setattr("cronico.main.ThreadPoolExecutor", FakeExecutor)
    monkeypatch.setattr("cronico.main.threading.Event", lambda: stop_event)
    monkeypatch.setattr(
        "cronico.main.signal.signal", lambda signum, handler: signal_handlers.setdefault(signum, handler)
    )
    monkeypatch.setattr(atexit, "register", lambda fn, path: registered_atexit.append((fn, path)))

    class FakeDateTime:
        @classmethod
        def now(cls):
            return time_points.pop(0) if time_points else datetime(2026, 1, 1, 12, 0, 0)

    monkeypatch.setattr("cronico.main.datetime", FakeDateTime)

    return {
        "load_calls": load_calls,
        "signal_handlers": signal_handlers,
        "registered_atexit": registered_atexit,
    }


def test_cmd_daemon_submits_due_task_and_shuts_down_cleanly(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    due_task = FakeTask("due", datetime(2026, 1, 1, 11, 59, 0))
    future_task = FakeTask("future", datetime(2026, 1, 1, 12, 5, 0))
    stop_event = FakeStopEvent(False)
    context = install_daemon_doubles(monkeypatch, [[future_task, due_task]], stop_event)

    wait_calls = 0

    def wait_and_stop(timeout: float) -> None:
        nonlocal wait_calls
        wait_calls += 1
        if wait_calls >= 2:
            stop_event.set()

    stop_event.wait = wait_and_stop
    args = SimpleNamespace(file=str(tmp_path / "cronico.yaml"), pidfile=str(tmp_path / "cronico.pid"), workers=3)

    cmd_daemon(args)

    observer = FakeObserver.instances[0]
    executor = FakeExecutor.instances[0]
    assert context["load_calls"] == [str(tmp_path / "cronico.yaml"), str(tmp_path / "cronico.yaml")]
    assert observer.started is True
    assert observer.stopped is True
    assert observer.joined is True
    assert executor.max_workers == 3
    assert executor.shutdown_wait is True
    assert due_task.pending_calls == 1
    assert executor.submitted == [due_task.run]
    assert future_task.pending_calls == 0


def test_cmd_daemon_skips_busy_tasks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    busy_due_task = FakeTask("busy", datetime(2026, 1, 1, 11, 59, 0), busy=True)
    context = install_daemon_doubles(monkeypatch, [[busy_due_task]], FakeStopEvent(True))
    args = SimpleNamespace(file=str(tmp_path / "cronico.yaml"), pidfile=str(tmp_path / "cronico.pid"), workers=2)

    cmd_daemon(args)

    executor = FakeExecutor.instances[0]
    assert context["load_calls"] == [str(tmp_path / "cronico.yaml"), str(tmp_path / "cronico.yaml")]
    assert executor.submitted == []
    assert busy_due_task.pending_calls == 0


def test_cmd_daemon_registers_signal_handlers_and_reloads_tasks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    initial = [FakeTask("first", datetime(2026, 1, 1, 12, 1, 0))]
    reloaded = [FakeTask("second", datetime(2026, 1, 1, 12, 2, 0))]
    context = install_daemon_doubles(monkeypatch, [initial, reloaded], FakeStopEvent(True))
    args = SimpleNamespace(file=str(tmp_path / "cronico.yaml"), pidfile=str(tmp_path / "cronico.pid"), workers=1)

    cmd_daemon(args)

    sighup_handler = context["signal_handlers"][signal.SIGHUP]
    sighup_handler(1, None)

    assert context["load_calls"] == [
        str(tmp_path / "cronico.yaml"),
        str(tmp_path / "cronico.yaml"),
        str(tmp_path / "cronico.yaml"),
    ]


def test_cmd_daemon_reloads_on_tasks_file_change(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tasks_file = tmp_path / "cronico.yaml"
    context = install_daemon_doubles(
        monkeypatch,
        [[FakeTask("first", datetime(2026, 1, 1, 12, 1, 0))], [FakeTask("second", datetime(2026, 1, 1, 12, 2, 0))]],
        FakeStopEvent(True),
    )
    args = SimpleNamespace(file=str(tasks_file), pidfile=str(tmp_path / "cronico.pid"), workers=1)

    cmd_daemon(args)

    observer = FakeObserver.instances[0]
    handler = observer.scheduled[0][0]
    handler.on_modified(SimpleNamespace(src_path=str(tasks_file)))
    handler.on_modified(SimpleNamespace(src_path=str(tmp_path / "other.yaml")))

    assert context["load_calls"] == [str(tasks_file), str(tasks_file), str(tasks_file)]


def test_cmd_daemon_signal_exit_sets_stop_event(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    stop_event = FakeStopEvent(True)
    context = install_daemon_doubles(
        monkeypatch,
        [[FakeTask("future", datetime(2026, 1, 1, 12, 1, 0))]],
        stop_event,
    )
    args = SimpleNamespace(file=str(tmp_path / "cronico.yaml"), pidfile=str(tmp_path / "cronico.pid"), workers=1)

    cmd_daemon(args)

    sigterm_handler = context["signal_handlers"][signal.SIGTERM]
    stop_event.flag = False
    sigterm_handler(signal.SIGTERM, None)

    assert stop_event.is_set() is True


def test_cmd_daemon_announces_future_task_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    future_task = FakeTask("future", datetime(2026, 1, 1, 12, 5, 0))
    stop_event = FakeStopEvent(False)
    install_daemon_doubles(monkeypatch, [[future_task]], stop_event)
    wait_calls = 0

    def wait_then_stop(timeout: float) -> None:
        nonlocal wait_calls
        wait_calls += 1
        if wait_calls >= 2:
            stop_event.set()

    stop_event.wait = wait_then_stop
    args = SimpleNamespace(file=str(tmp_path / "cronico.yaml"), pidfile=str(tmp_path / "cronico.pid"), workers=1)

    cmd_daemon(args)

    out = capsys.readouterr().out
    assert out.count("Next task to run: 'future'") == 1
