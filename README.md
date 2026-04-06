# Cronico

Cronico is a small YAML-based scheduler for Unix-like systems.

You describe tasks in a single YAML file, start the daemon, and Cronico takes care of running them on schedule. It supports classic cron expressions, cron aliases such as `@daily`, optional seconds, retries, timeouts, `.env` files, custom working directories, and per-task log files.

If you want something simpler than a full job platform but more flexible than system cron entries scattered across machines, this is the idea.

## Installation

Install it from PyPI:

```bash
pip install cronico
```

Check that the CLI is available:

```bash
cronico --version
```

## Quick Start

Create a `cronico.yaml` file:

```yaml
tasks:
  hello:
    cron: "@daily"
    command: "echo 'Hello from Cronico'"
```

List the configured tasks:

```bash
cronico list
```

Run one task immediately:

```bash
cronico run hello
```

Start the scheduler:

```bash
cronico daemon
```

By default Cronico looks for `cronico.yaml` in the current directory.

## How Cronico Works

Cronico has two main modes:

- `cronico list` reads the YAML file and shows the configured tasks.
- `cronico run <name>` runs one task immediately, without waiting for its schedule.
- `cronico daemon` keeps running in the foreground and launches tasks when they are due.

The daemon is designed to be supervised by something else, usually `systemd`, `supervisord`, a container runtime, or any process manager you already use.

## Tasks File

The tasks file is a YAML document with a top-level `tasks` mapping. Each key is the task name, and each value is that task's configuration.

Basic example:

```yaml
tasks:
  backup:
    description: "Daily database dump"
    cron: "0 2 * * *"
    command: "./scripts/backup.sh"
```

More complete example:

```yaml
tasks:
  hello_world:
    description: "A simple command every 5 minutes"
    cron: "*/5 * * * *"
    command: "echo 'Hello, World!'"
    retry_on_error: true
    max_attempts: 3
    timeout: 60
    env_file: ".env"
    working_dir: "/srv/my-app"
    environment:
      APP_ENV: "production"
    log_file: "/var/log/cronico/$NAME-$TIMESTAMP.log"

  report:
    description: |
      Runs every day at 03:00:15
      and stores output in a task-specific file.
    cron:
      minute: 0
      hour: 3
      day: "*"
      month: "*"
      weekday: "*"
      second: 15
    command: |
      echo "Generating report..."
      ./bin/report
```

## Configuration Reference

Each task supports the following fields.

### `description`

Optional text used only for display in `cronico list`.

```yaml
description: "Nightly sync job"
```

### `cron`

Required. Defines when the task should run.

Accepted forms:

- A classic cron string such as `"*/5 * * * *"`
- A cron string with seconds such as `"*/1 * * * * 0,30"`
- A supported alias such as `"@daily"`
- A YAML mapping with named fields

Examples:

```yaml
cron: "*/10 * * * *"
```

```yaml
cron: "@hourly"
```

```yaml
cron:
  minute: 30
  hour: 1
  day: "*"
  month: "*"
  weekday: 1-5
```

```yaml
cron:
  minute: "*"
  hour: "*"
  day: "*"
  month: "*"
  weekday: "*"
  second: 10
```

Supported aliases:

- `@yearly`
- `@annually`
- `@monthly`
- `@weekly`
- `@daily`
- `@midnight`
- `@hourly`

### `command`

Required. The shell command to run.

This can be a one-liner:

```yaml
command: "echo 'hello'"
```

Or a multiline block:

```yaml
command: |
  echo "step 1"
  echo "step 2"
```

Cronico runs commands with `shell=True`, so shell syntax such as pipes, redirection, command substitution, and environment expansion works as you would expect.

### `retry_on_error`

Optional boolean. Default: `false`.

If enabled, Cronico retries the task when the command exits with a non-zero status.

```yaml
retry_on_error: true
```

### `max_attempts`

Optional integer. Default: `1`.

Maximum number of attempts for that run. This includes the first execution.

```yaml
max_attempts: 3
```

If `retry_on_error` is `false`, Cronico stops after the first failure even if `max_attempts` is greater than `1`.

### `timeout`

Optional number of seconds. If the process runs longer than this, Cronico kills it.

```yaml
timeout: 120
```

### `env_file`

Optional path to a `.env` file. If the file exists, Cronico loads it before starting the command.

```yaml
env_file: ".env"
```

### `environment`

Optional mapping of inline environment variables.

```yaml
environment:
  API_URL: "https://example.com"
  LOG_LEVEL: "info"
```

Environment precedence is:

1. Current process environment
2. Variables loaded from `env_file`
3. Variables defined in `environment`

In practice, inline `environment` values win.

### `working_dir`

Optional working directory for the command. If omitted, Cronico uses the current working directory.

```yaml
working_dir: "/srv/my-project"
```

### `log_file`

Optional path for a file log handler. When set, Cronico writes task logs both to standard output and to the file.

```yaml
log_file: "/var/log/cronico/$NAME-$TASKID.log"
```

Cronico creates the parent directory if needed.

You can use variable expansion in the path. The following variables are available:

- `$NAME`: task name
- `$TASKID`: short per-run identifier
- `$WORKING_DIR`: resolved working directory
- `$TIMESTAMP`: run timestamp in `YYYYMMDDHHMMSS`

Both `$VAR` and `${VAR}` are supported.

### `log_format_string`

Optional logging format for the console output.

Default:

```text
%(asctime)s [%(levelname)s] %(message)s
```

Example:

```yaml
log_format_string: "[%(levelname)s] %(message)s"
```

### `log_file_format_string`

Optional logging format for the file output when `log_file` is enabled.

Default:

```text
%(asctime)s [%(levelname)s] %(message)s
```

## Writing Commands

### Shell commands

Simple shell commands work out of the box:

```yaml
command: "python manage.py clearsessions"
```

### Multiline shell scripts

You can write longer scripts directly in YAML:

```yaml
command: |
  set -eu
  cd /srv/my-app
  ./bin/sync-data
  ./bin/build-cache
```

### Shebang scripts

If the command starts with a shebang, Cronico writes the body to a temporary file and runs it with that interpreter.

Python example:

```yaml
command: |
  #!/usr/bin/env python3

  import datetime
  print("Hello from Python at", datetime.datetime.now())
```

Perl example:

```yaml
command: |
  #!/usr/bin/env perl

  use strict;
  use warnings;
  my ($sec,$min,$hour) = localtime();
  print "Hello from Perl at $hour:$min:$sec\n";
```

This is handy when a task is a bit too long for a one-liner but you still want to keep it close to the scheduler config.

## Cron Syntax

Cronico uses `croniter` to parse schedules.

The most common formats are:

- Five fields: minute, hour, day of month, month, day of week
- Six fields when you want seconds support

Examples:

```yaml
cron: "*/5 * * * *"
```

Every five minutes.

```yaml
cron: "0 2 * * *"
```

Every day at 02:00.

```yaml
cron: "*/1 * * * * 0,30"
```

Every minute at second 0 and 30.

```yaml
cron:
  minute: 0
  hour: 9
  weekday: 1-5
```

Monday to Friday at 09:00.

If the cron expression is invalid, Cronico exits with an error when loading the file.

## CLI Reference

### Global option: `--file`

Use a custom tasks file:

```bash
cronico --file /etc/cronico/tasks.yaml list
```

If you do not pass `--file`, Cronico uses:

- the value of `CRONICO_TASKS_FILE`, if set
- otherwise `cronico.yaml`

Example:

```bash
export CRONICO_TASKS_FILE=/etc/cronico/tasks.yaml
cronico list
```

### `cronico list`

Reads the tasks file and prints a summary of configured tasks.

```bash
cronico list
```

### `cronico run <name>`

Runs one task immediately by name.

```bash
cronico run backup
```

This is useful for testing a task before leaving it to the daemon.

### `cronico daemon`

Starts the scheduler loop in the foreground.

```bash
cronico daemon
```

Options:

- `--workers`: maximum number of concurrent task workers. Default: `10`
- `--pidfile`: lock file path. Default: `/tmp/cronico.pid`

Example:

```bash
cronico --file /etc/cronico/tasks.yaml daemon --workers 4 --pidfile /run/cronico.pid
```

### `cronico template`

Prints a sample tasks file that you can use as a starting point.

```bash
cronico template
```

## Running as a Daemon

Cronico's daemon runs in the foreground, so it works best under a service manager.

Typical flow:

1. Create the tasks file.
2. Test tasks with `cronico list` and `cronico run <name>`.
3. Start `cronico daemon` under `systemd` or another supervisor.

Cronico uses a PID lock file to avoid multiple scheduler instances using the same pidfile.

If the pidfile points to a dead process, Cronico removes it automatically.

## Reloading Configuration

Cronico reloads the tasks file in two ways:

- when it receives `SIGHUP`
- when the tasks file is modified on disk

That means you can update the YAML and let the daemon pick it up without a full restart.

## Logging

Cronico logs task lifecycle events such as:

- task start
- attempt number
- stdout lines
- stderr lines
- timeout errors
- process exit code

By default logs go to standard output, with warnings and fatal errors going to standard error.

If `log_file` is configured, the same task logger also writes to a file.

## Example Configurations

### Simple recurring shell command

```yaml
tasks:
  ping:
    cron: "*/5 * * * *"
    command: "echo 'still alive'"
```

### Command with environment variables

```yaml
tasks:
  greet:
    cron: "@hourly"
    environment:
      GREETING: "Hola"
    command: |
      echo "$GREETING from $(hostname)"
```

### `.env` file plus inline overrides

```yaml
tasks:
  sync:
    cron: "0 * * * *"
    env_file: ".env"
    environment:
      LOG_LEVEL: "debug"
    command: "./bin/sync"
```

### Separate log file per run

```yaml
tasks:
  report:
    cron: "0 6 * * *"
    log_file: "./logs/$NAME-${TIMESTAMP}.log"
    command: "./bin/report"
```

### Task written as a Python script

```yaml
tasks:
  python_example:
    cron: "*/10 * * * *"
    command: |
      #!/usr/bin/env python3

      import os
      print("running in", os.getcwd())
```

## Common Gotchas

### The daemon stays in the foreground

This is expected. Use a supervisor if you want it managed in the background.

### `cronico run` ignores the schedule

Also expected. `run` is a manual execution command.

### Relative paths depend on where you start Cronico

If you use relative `env_file`, `log_file`, or `command` paths, think about the working directory from which the daemon starts. If you want stable behavior, prefer absolute paths or set `working_dir`.

### `max_attempts` alone does not enable retries

To retry failures, you need both:

```yaml
retry_on_error: true
max_attempts: 3
```

### Invalid YAML or cron expressions stop startup

Cronico validates the file when loading it. If something is wrong, it exits with a clear error instead of silently skipping tasks.

## Troubleshooting

### Check whether the file is being loaded

Run:

```bash
cronico --file /path/to/tasks.yaml list
```

Cronico prints the file path it is using, which helps catch wrong paths and wrong working directories.

### Test one task in isolation

Run:

```bash
cronico --file /path/to/tasks.yaml run task_name
```

This is usually the quickest way to debug command issues.

### Watch task output

If the daemon is running in the foreground, stdout and stderr are visible directly. If not, check your supervisor logs or configure `log_file`.

### Stale pidfile

Cronico removes stale pidfiles automatically. If you are using a custom `--pidfile`, make sure the process has permission to create and delete it.

## Development

Clone the repository and install the development dependencies:

```bash
pip install -e ".[dev]"
```

Run the test suite:

```bash
make test
```

Run linting:

```bash
make lint
```

## License

MIT
