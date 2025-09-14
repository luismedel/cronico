# Cronico

Cronico is a lightweight, YAML-based task scheduler for Unix-like systems.

It lets you define recurring jobs with flexible cron expressions — supporting traditional minute-based syntax, extended formats with seconds, and common shorthand aliases (@daily, @hourly, etc.).

Tasks can include:
-	Retry policies with configurable attempts.
-	Timeouts to kill long-running processes.
-	Environment injection from .env files or inline variables.
-	Working directory control per task.
- Streaming or buffered logs for stdout/stderr.

Cronico is designed to run as a long-lived daemon (via systemd or similar) and can reload its configuration on SIGHUP without restarting the process.

```yaml
tasks:
  # Classic: every 5 minutes
  example_task:
    cron: "*/5 * * * *"
    command: "echo 'Hello, World!'"
    retry_on_error: true
    max_attempts: 3
    env_file: ".env"
    timeout: 60  # seconds
    working_dir: "/path/to/dir"
    environment:
      MY_VAR: "value"

  # Extended with seconds: every minute, at the 10th second
  every_minute_at_second_10:
    cron:
      minute: "*"
      hour: "*"
      day: "*"
      month: "*"
      weekday: "*"
      second: 10
    command: "echo 'Run at second 10 of every minute'"

  # Classic with seconds: every 30 seconds
  every_30_seconds:
    cron: "*/1 * * * * 0,30"
    command: "echo 'This runs at second 0 and 30 of each minute'"

  # Daily at 03:00:15
  daily_with_seconds:
    cron:
      minute: 0
      hour: 3
      day: "*"
      month: "*"
      weekday: "*"
      second: 15
    command: "echo 'Daily at 03:00:15'"

  # Shorthand: daily, at 00:00
  short_hand:
    cron: "@daily"
    command: |
        echo "Supported aliases:"
        echo "- @yearly: 0 0 1 1 *"
        echo "- @annually: 0 0 1 1 *"
        echo "- @monthly: 0 0 1 * *"
        echo "- @weekly: 0 0 * * 0"
        echo "- @daily: 0 0 * * *"
        echo "- @midnight: 0 0 * * *"
        echo "- @hourly: 0 * * * *"
```
