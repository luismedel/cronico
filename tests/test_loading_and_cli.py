import argparse
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from cronico.main import cmd_list, cmd_run, cmd_template, file_command, load_tasks, main


def write_tasks_file(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_load_tasks_reads_valid_yaml(tmp_path: Path) -> None:
    tasks_file = write_tasks_file(
        tmp_path / "cronico.yaml",
        """
tasks:
  first:
    cron: "@daily"
    command: "echo first"
  second:
    cron: "*/5 * * * *"
    command: "echo second"
""",
    )

    tasks = load_tasks(str(tasks_file))

    assert [task.name for task in tasks] == ["first", "second"]
    assert tasks[0].cron == "0 0 * * *"


def test_load_tasks_rejects_yaml_without_tasks_section(tmp_path: Path) -> None:
    tasks_file = write_tasks_file(tmp_path / "cronico.yaml", "foo: bar\n")

    with pytest.raises(SystemExit, match="1"):
        load_tasks(str(tasks_file))


def test_load_tasks_rejects_invalid_task_configuration(tmp_path: Path) -> None:
    tasks_file = write_tasks_file(
        tmp_path / "cronico.yaml",
        """
tasks:
  broken:
    cron: "@daily"
""",
    )

    with pytest.raises(SystemExit, match="1"):
        load_tasks(str(tasks_file))


def test_load_tasks_rejects_task_without_cron_after_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks_file = write_tasks_file(
        tmp_path / "cronico.yaml",
        """
tasks:
  broken:
    cron: "@daily"
    command: "echo hi"
""",
    )

    class FakeTask:
        def __init__(self, name: str, cfg: dict) -> None:
            self.name = name
            self.cron = ""
            self.command = "echo hi"

    monkeypatch.setattr("cronico.main.Task", FakeTask)

    with pytest.raises(SystemExit, match="1"):
        load_tasks(str(tasks_file))


def test_load_tasks_rejects_task_without_command_after_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks_file = write_tasks_file(
        tmp_path / "cronico.yaml",
        """
tasks:
  broken:
    cron: "@daily"
    command: "echo hi"
""",
    )

    class FakeTask:
        def __init__(self, name: str, cfg: dict) -> None:
            self.name = name
            self.cron = "@daily"
            self.command = ""

    monkeypatch.setattr("cronico.main.Task", FakeTask)

    with pytest.raises(SystemExit, match="1"):
        load_tasks(str(tasks_file))


def test_file_command_uses_cli_file_argument(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tasks_file = write_tasks_file(
        tmp_path / "cronico.yaml",
        """
tasks:
  example:
    cron: "@daily"
    command: "echo hello"
""",
    )
    seen: dict[str, object] = {}

    @file_command
    def fake_cmd(tasks, tasks_file_arg, args):
        seen["tasks"] = tasks
        seen["tasks_file"] = tasks_file_arg
        seen["args"] = args

    fake_cmd(SimpleNamespace(file=str(tasks_file)))

    out = capsys.readouterr().out
    assert f"Using tasks file {tasks_file}" in out
    assert seen["tasks_file"] == str(tasks_file)
    assert len(seen["tasks"]) == 1


def test_file_command_uses_environment_default_when_file_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    tasks_file = write_tasks_file(
        tmp_path / "from-env.yaml",
        """
tasks:
  example:
    cron: "@daily"
    command: "echo hello"
""",
    )
    monkeypatch.setattr("cronico.main.TASKS_FILE", str(tasks_file))
    seen: dict[str, str] = {}

    @file_command
    def fake_cmd(tasks, tasks_file_arg, args):
        seen["tasks_file"] = tasks_file_arg

    fake_cmd(SimpleNamespace(file=None))

    assert seen["tasks_file"] == str(tasks_file)
    assert f"Using tasks file {tasks_file}" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (FileNotFoundError(), "not found"),
        (yaml.YAMLError("bad yaml"), "Error parsing YAML file"),
        (RuntimeError("boom"), "Unexpected error parsing tasks file"),
    ],
)
def test_file_command_handles_loading_errors(
    monkeypatch: pytest.MonkeyPatch,
    exc: Exception,
    expected: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    @file_command
    def fake_cmd(tasks, tasks_file_arg, args):
        raise AssertionError("should not run")

    def fake_load_tasks(tasks_file: str):
        raise exc

    monkeypatch.setattr("cronico.main.load_tasks", fake_load_tasks)

    with pytest.raises(SystemExit, match="1"):
        fake_cmd(SimpleNamespace(file="missing.yaml"))

    assert expected in capsys.readouterr().err


def test_cmd_list_prints_description_and_expanded_alias(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tasks_file = write_tasks_file(
        tmp_path / "cronico.yaml",
        """
tasks:
  example:
    description: Demo task
    cron: "@daily"
    command: |
      echo hello
      echo again
""",
    )

    cmd_list(SimpleNamespace(file=str(tasks_file)))

    out = capsys.readouterr().out
    assert "Configured tasks:" in out
    assert "example - Demo task" in out
    assert "cron='@daily (0 0 * * *)'" in out
    assert "command='echo hello'..." in out


def test_cmd_run_executes_named_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tasks_file = write_tasks_file(
        tmp_path / "cronico.yaml",
        """
tasks:
  example:
    cron: "@daily"
    command: "echo hello"
""",
    )
    called: list[str] = []

    def fake_run(self) -> None:
        called.append(self.name)

    monkeypatch.setattr("cronico.main.Task.run", fake_run)

    cmd_run(SimpleNamespace(file=str(tasks_file), name="example"))

    assert called == ["example"]


def test_cmd_run_exits_when_task_is_missing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tasks_file = write_tasks_file(
        tmp_path / "cronico.yaml",
        """
tasks:
  example:
    cron: "@daily"
    command: "echo hello"
""",
    )

    with pytest.raises(SystemExit, match="1"):
        cmd_run(SimpleNamespace(file=str(tasks_file), name="missing"))

    assert "Task 'missing' not found" in capsys.readouterr().err


def test_cmd_template_prints_expected_yaml(capsys: pytest.CaptureFixture[str]) -> None:
    cmd_template(SimpleNamespace())

    out = capsys.readouterr().out
    assert "tasks:" in out
    assert "example_task:" in out
    assert "another_task:" in out


def test_main_parses_cli_and_dispatches(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    called: dict[str, object] = {}

    def fake_cmd(args: argparse.Namespace) -> None:
        called["command"] = args.command
        called["file"] = args.file

    monkeypatch.setattr("cronico.main.cmd_list", fake_cmd)
    monkeypatch.setattr(sys, "argv", ["cronico", "--file", "tasks.yaml", "list"])

    main()

    out = capsys.readouterr().out
    assert "cronico " in out
    assert called == {"command": "list", "file": "tasks.yaml"}
