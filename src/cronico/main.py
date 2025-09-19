import argparse
import logging
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import IO, Callable
from uuid import uuid4

import yaml
from croniter import CroniterBadCronError, croniter
from dotenv import dotenv_values

from cronico import __version__

CRON_ALIASES = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}

DEFAULT_TASKS_FILENAME = "cronico.yaml"
ENV_VAR_NAME = "CRONICO_TASKS_FILE"
MAX_WORKERS = 10
LOCK_FILE = "/tmp/cronico.pid"

TASKS_FILE = os.environ.get(ENV_VAR_NAME, DEFAULT_TASKS_FILENAME)


def info(msg: str) -> None:
    print(msg, flush=True)


def warning(msg: str) -> None:
    print(f"WARN : {msg}", file=sys.stderr, flush=True)


def error(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr, flush=True)


def critical(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


# Blatantly adapted from Python 3.12's os.path.expandvars
#
# Expand paths containing shell variable substitutions.
# This expands the forms $variable and ${variable} only.
# Non-existent variables are left unchanged.

R_VAR = re.compile(r"\$(\w+|\{[^}]*\})", re.ASCII)


def expandvars(path: str, environ: dict) -> str:
    """Expand shell variables of form $var and ${var}.  Unknown variables
    are left unchanged.

    >>> expandvars('abc$def$ghi${jkl}mno', {'def': 'D', 'jkl': 'J'})
    'abcDghiJmno'
    >>> expandvars('abc$def$ghi${jkl}mno', {})
    'abc$def$ghi${jkl}mno'
    >>> expandvars('abc$def$ghi $jkl mno', {'def': 'D', 'jkl': 'J'})
    'abcDghi J mno'
    """

    if "$" not in path:
        return path

    start = "{"
    end = "}"

    i = 0
    while True:
        m = R_VAR.search(path, i)
        if not m:
            break
        i, j = m.span(0)
        name: str = m.group(1)
        if name.startswith(start) and name.endswith(end):
            name = name[1:-1]
        try:
            value = environ[name]
        except KeyError:
            i = j
        else:
            tail = path[j:]
            path = path[:i] + value
            i = len(path)
            path += tail
    return path


def parse_cron(cron_cfg: str | dict) -> str:
    expr: str
    if isinstance(cron_cfg, str):
        expr = cron_cfg.strip()
        if expr.startswith("@"):
            if expr not in CRON_ALIASES:
                raise ValueError(f"Unknown cron alias: {expr}")
            expr = CRON_ALIASES[cron_cfg.strip()]
    elif isinstance(cron_cfg, dict):
        minute = str(cron_cfg.get("minute", "*"))
        hour = str(cron_cfg.get("hour", "*"))
        day = str(cron_cfg.get("day", "*"))
        month = str(cron_cfg.get("month", "*"))
        weekday = str(cron_cfg.get("weekday", "*"))
        expr = f"{minute} {hour} {day} {month} {weekday}"
        if "second" in cron_cfg:
            second = str(cron_cfg["second"])
            expr = f"{expr} {second}"

    else:
        raise ValueError(f"Invalid cron format: {cron_cfg!r}")

    try:
        croniter(expr)
    except CroniterBadCronError as e:
        raise ValueError(f"Invalid cron expression {expr!r}: {e}")

    return expr


def extract_shebang(command: str) -> str | None:
    head = command.lstrip().splitlines()[0]
    if head.startswith("#!"):
        return head[2:].strip()
    return None


def extract_script_body(command: str) -> str:
    lines = command.lstrip().splitlines()
    if lines and lines[0].startswith("#!"):
        return "\n".join(lines[1:]).lstrip()
    return command.lstrip()


def run_task(task: "Task") -> int:
    env = os.environ.copy()
    if task.env_file and Path(task.env_file).exists():
        env.update(dotenv_values(task.env_file))  # type: ignore
    env.update(task.environment)

    command = task.command

    tmp_path: str | None = None
    shebang = extract_shebang(command)
    if shebang:
        script_body = extract_script_body(command)
        if not script_body.strip():
            task.logger.error("No script body found after shebang")
            return 1

        with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp:
            tmp.write(script_body)
            os.chmod(tmp.name, 0o700)
            tmp_path = tmp.name
            command = f"{shebang} {tmp_path}"

    try:
        start = time.monotonic()
        process = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            bufsize=1,
            cwd=task.working_dir if task.working_dir else os.getcwd(),
        )

        if not task.stream_output:

            def output_buffer(buf: str, fn: Callable[[str], None]) -> None:
                if buf:
                    for line in buf.splitlines():
                        fn(line)

            try:
                stdout, stderr = process.communicate(timeout=task.timeout)
                output_buffer(stdout, task.logger.info)
                output_buffer(stderr, task.logger.error)
            except subprocess.TimeoutExpired:
                task.logger.error(f"Timeout after {task.timeout}s, killing process...")
                process.kill()
                process.wait()
                stdout, stderr = process.communicate()
                output_buffer(stdout, task.logger.info)
                output_buffer(stderr, task.logger.error)
                raise
        else:

            def reader(stream: IO[str], log_fn: Callable[[str], None]) -> None:
                for line in iter(stream.readline, ""):
                    log_fn(line.rstrip())
                stream.close()

            t_out = threading.Thread(target=reader, args=(process.stdout, task.logger.info))
            t_err = threading.Thread(target=reader, args=(process.stderr, task.logger.error))
            threads = [t_out, t_err]
            for t in threads:
                t.daemon = True
                t.start()

            try:
                while True:
                    if task.timeout and (time.monotonic() - start) > task.timeout:
                        task.logger.error(f"Timeout after {task.timeout}s, killing process...")
                        process.kill()
                        process.wait()
                        break
                    if process.poll() is not None:
                        break
                    time.sleep(0.1)
            finally:
                for t in threads:
                    t.join()

        task.logger.info(f"Process exited with code {process.returncode}")
        return process.returncode
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except FileNotFoundError:
                pass


class Task:
    def __init__(self, name: str, cfg: dict) -> None:
        self.name = name
        self.description: str | None = cfg.get("description")
        self.raw_cron: str | dict | None = cfg.get("cron", None)
        if self.raw_cron is None:
            raise ValueError("Invalid or missing cron schedule")
        self.cron: str = parse_cron(self.raw_cron)
        self.command: str = cfg.get("command", "")
        if not self.command:
            raise ValueError("Missing command to run")

        self.stream_output: bool = cfg.get("stream_output", True)

        self.retry_on_error: bool = cfg.get("retry_on_error", False)
        self.max_attempts = int(cfg.get("max_attempts", 1))

        timeout = cfg.get("timeout")
        self.timeout: float | None = float(timeout) if timeout is not None else None
        
        self.env_file = cfg.get("env_file")
        self.environment = cfg.get("environment") or {}
        self.working_dir = cfg.get("working_dir") or None

        self.log_file: str | None = cfg.get("log_file")
        self.logger = logging.Logger(self.name, level=logging.INFO)
        self.logger.handlers.clear()
        self.logger.addHandler(logging.StreamHandler(sys.stdout))

        self.last_run: datetime | None = None
        self.next_run: datetime = None  # type: ignore
        self.calculate_next_run()

        self._running = False
        self._pending = False

    @property
    def is_busy(self) -> bool:
        return self._running or self._pending

    def mark_pending(self) -> None:
        if self._running:
            raise RuntimeError("Cannot mark a running task as pending")
        self._pending = True

    def calculate_next_run(self, from_: datetime | None = None) -> None:
        start_time = from_ or datetime.now()
        self.next_run = croniter(self.cron, start_time).get_next(datetime)

    def run(self) -> None:
        task_id = uuid4().hex[:8]

        self._running = True
        self.last_run = datetime.now()

        env = os.environ.copy()
        if self.env_file and Path(self.env_file).exists():
            env.update(dotenv_values(self.env_file))  # type: ignore
        env.update(self.environment)

        if self.log_file:
            path_env = dict(env)
            path_env["NAME"] = self.name
            path_env["TASKID"] = task_id
            path_env["TIMESTAMP"] = self.last_run.strftime("%Y%m%d%H%M%S")

            path = expandvars(self.log_file, path_env)

            self.logger.addHandler(logging.FileHandler(path, mode="a", encoding="utf-8"))

        try:
            self.logger.info(f"Starting task '{self.name}' (id={task_id}) on {self.last_run.isoformat()}")
            attempts = 0
            success = False

            while attempts < self.max_attempts and not success:
                attempts += 1
                self.logger.info(f"Attempt {attempts}/{self.max_attempts}")
                try:
                    returncode = run_task(self)
                    if returncode == 0:
                        self.logger.info("Finished successfully")
                        success = True
                    else:
                        self.logger.error(f"Failed (exit {returncode})")
                except subprocess.TimeoutExpired:
                    self.logger.error("Timeout expired")
                except Exception as e:
                    self.logger.error(f"Exception: {e}")

                if not success and not self.retry_on_error:
                    break

        finally:
            self._running = False
            self._pending = False
            self.calculate_next_run(datetime.now())


def load_tasks(tasks_file: str) -> list[Task]:
    result = []
    with open(tasks_file) as f:
        data = yaml.safe_load(f) or {}

        if "tasks" not in data:
            critical("No 'tasks' section found in the tasks file")

        tasks = data["tasks"]
        assert isinstance(tasks, dict)

        for name, cfg in tasks.items():
            try:
                task = Task(name, cfg)
                if not task.cron:
                    critical(f"Task '{name}' is missing a cron schedule.")
                if not task.command:
                    critical(f"Task '{name}' is missing a command to run.")
                result.append(task)
            except ValueError as e:
                critical(f"Task '{name}' has invalid configuration: {e}.")
    return result


def file_command(fn: Callable[[list[Task], str, argparse.Namespace], None]) -> Callable[[argparse.Namespace], None]:
    def wrapper(args: argparse.Namespace) -> None:
        tasks_file = args.file or TASKS_FILE
        info(f"Using tasks file {tasks_file}")

        try:
            tasks = load_tasks(tasks_file)
            fn(tasks, tasks_file, args)
        except FileNotFoundError:
            critical(f"Tasks file '{tasks_file}' not found")
        except yaml.YAMLError as e:
            critical(f"Error parsing YAML file '{tasks_file}': {e}")
        except Exception as e:
            critical(f"Unexpected error parsing tasks file '{tasks_file}': {e}")

    return wrapper


@file_command
def cmd_list(tasks: list[Task], tasks_file: str, args: argparse.Namespace) -> None:
    info("Configured tasks:")
    for task in tasks:
        task_id = task.name
        if task.description:
            task_id = f"{task_id} - {task.description.strip()}"
        cron_expr = task.raw_cron
        if task.raw_cron != task.cron:
            cron_expr = f"{task.raw_cron} ({task.cron})"
        info(f" - {task_id}: cron='{cron_expr}' command='{task.command.splitlines()[0]}'...")


@file_command
def cmd_run(tasks: list[Task], tasks_file: str, args: argparse.Namespace) -> None:
    name = args.name

    task = next((t for t in tasks if t.name == name), None)
    if not task:
        error(f"Task '{name}' not found")
        sys.exit(1)
    task.run()


@file_command
def cmd_daemon(tasks: list[Task], tasks_file: str, args: argparse.Namespace) -> None:
    import atexit

    pidfile = args.pidfile
    check_lockfile(pidfile)
    atexit.register(remove_lockfile, path=pidfile)

    stop_event = threading.Event()

    def load_tasks_file(signum: int | None = None, frame=None) -> None:
        if signum is not None:
            signal_name = signal.Signals(signum).name
            info(f"Received signal {signal_name} ({signum}).")

        nonlocal tasks
        try:
            tasks = load_tasks(tasks_file)
            info(f"Loaded {len(tasks)} tasks")
        except Exception as e:
            error(f"Error reloading tasks: {e}")

    def signal_exit(signum, _) -> None:
        signal_name = signal.Signals(signum).name
        info(f"Received signal {signal_name} ({signum}).")
        if signum in (signal.SIGINT, signal.SIGTERM):
            stop_event.set()
        else:
            warning(f"Unhandled signal {signal_name} ({signum}), ignoring...")

    signal.signal(signal.SIGINT, signal_exit)
    signal.signal(signal.SIGTERM, signal_exit)
    signal.signal(signal.SIGHUP, load_tasks_file)

    load_tasks_file()
    executor = ThreadPoolExecutor(max_workers=args.workers)

    try:
        next_task: Task | None = None
        while not stop_event.is_set():
            sorted_tasks: list[Task] = sorted(tasks, key=lambda t: t.next_run)  # type: ignore
            task: Task | None = next((t for t in sorted_tasks if t.next_run and not t.is_busy), None)
            if not task:
                stop_event.wait(1)
                continue

            next_run = task.next_run
            assert next_run is not None

            if next_run > datetime.now():
                if task != next_task:
                    next_task = task
                    info(f"Next task to run: '{task.name}' at {next_run}")
                stop_event.wait(1)
                continue

            if stop_event.is_set():
                break

            next_task = None
            task.mark_pending()
            _ = executor.submit(task.run)
    finally:
        info("Shutting down...")
        executor.shutdown(wait=True)
    info("Exited cleanly.")


def cmd_template(args: argparse.Namespace) -> None:
    TPL = """
tasks:
    example_task:
        cron: "*/5 * * * *"  # every 5 minutes
        command: "echo 'Hello, World!'"
        retry_on_error: true
        max_attempts: 3
        env_file: ".env"
        timeout: 60  # seconds
        working_dir: "/path/to/dir"
        environment:
            MY_VAR: "value"

    another_task:
        cron:
            minute: 0
            hour: 3
            day: "*"
            month: "*"
            weekday: 1-5
        command: |
            echo "This is a multi-line command"
            echo "It runs every day at midnight"
"""
    print(TPL)


def main() -> None:
    parser = argparse.ArgumentParser(prog="cronico", description="Cronico: another YAML-based scheduler")
    subparsers = parser.add_subparsers(dest="command", required=True)

    parser.add_argument("--version", action="version", version=f"cronico {__version__}")

    parser.add_argument(
        "--file",
        type=str,
        default=TASKS_FILE,
        help=f"Path to tasks YAML file (default: ${ENV_VAR_NAME} or {DEFAULT_TASKS_FILENAME})",
    )

    p_list = subparsers.add_parser("list", help="List all configured tasks")
    p_list.set_defaults(func=cmd_list)

    p_run = subparsers.add_parser("run", help="Run a specific task immediately")
    p_run.add_argument("name", help="Task name", type=str)
    p_run.set_defaults(func=cmd_run)

    p_daemon = subparsers.add_parser("daemon", help="Start the scheduler loop")
    p_daemon.set_defaults(func=cmd_daemon)
    p_daemon.add_argument(
        "--workers", type=int, default=10, help=f"Number of concurrent workers (default: {MAX_WORKERS})"
    )
    p_daemon.add_argument("--pidfile", type=str, help=f"Path to PID file (default: {LOCK_FILE})", default=LOCK_FILE)

    p_template = subparsers.add_parser("template", help="Output a pair of sample tasks")
    p_template.set_defaults(func=cmd_template)

    args = parser.parse_args()

    print(f"cronico {__version__}")
    args.func(args)


def check_lockfile(path: str) -> None:
    if os.path.exists(path):
        old_pid: int = -1

        try:
            with open(path, "r") as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)
        except ValueError:
            warning("Corrupt lockfile, removing...")
            os.remove(path)
        except ProcessLookupError:
            warning(f"Removing stale lockfile (pid {old_pid})")
            os.remove(path)
        else:
            critical(f"cronico already running with PID {old_pid}")

    with open(path, "w") as f:
        f.write(str(os.getpid()))


def remove_lockfile(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception as e:
        warning(f"Could not remove lockfile: {e}")


if __name__ == "__main__":
    main()
