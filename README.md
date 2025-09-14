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
  example_task:
    description: |
      Classic cron expression: every 5 minutes
    cron: "*/5 * * * *"
    command: "echo 'Hello, World!'"
    retry_on_error: true
    max_attempts: 3
    env_file: ".env"
    timeout: 60  # seconds
    working_dir: "/path/to/dir"
    environment:
      MY_VAR: "value"

  custom_env:
    cron: "*/5 * * * *"
    environment:
      GREETING: "Hola"
    command: |
      echo "$GREETING from Bash at $(date)"

  every_minute_at_second_10:
    description: |
      Extended with seconds: every minute, at the 10th second
    cron:
      minute: "*"
      hour: "*"
      day: "*"
      month: "*"
      weekday: "*"
      second: 10
    command: "echo 'Run at second 10 of every minute'"

  every_30_seconds:
    description: |
      Classic with seconds: every 30 seconds
    cron: "*/1 * * * * 0,30"
    command: "echo 'This runs at second 0 and 30 of each minute'"

  daily_with_seconds:
    description: |
      Daily at 03:00:15
    cron:
      minute: 0
      hour: 3
      day: "*"
      month: "*"
      weekday: "*"
      second: 15
    command: "echo 'Daily at 03:00:15'"

  shorthand:
    description: |
      Shorthand: daily, at 00:00
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

  with_shebang:
    description: |
      You can use a shebang to specify the interpreter to use.
    cron: "*/10 * * * *"
    command: |
      #!/usr/bin/env python3

      import datetime
      print("Hello from Python at", datetime.datetime.now())

  another_shebang_example:
    description: |
      Another one...
    cron: "*/10 * * * *"
    command: |
      #!/usr/bin/env perl

      use strict;
      use warnings;
      my ($sec,$min,$hour) = localtime();
      print "Hello from Perl at $hour:$min:$sec\n";

  and_another_shebang_example:
    description: |
      I think you get the idea.
    cron: "*/10 * * * *"
    command: |
      #!/usr/bin/env perl

      use strict;
      use warnings;
      my ($sec,$min,$hour) = localtime();
      print "Hello from Perl at $hour:$min:$sec\n";
```
