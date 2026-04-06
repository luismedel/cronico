from __future__ import annotations

import pytest

from cronico.main import extract_script_body, extract_shebang, expandvars, parse_cron


def test_expandvars_expands_known_variables() -> None:
    result = expandvars("$HOME/${USER}/logs", {"HOME": "/tmp/home", "USER": "alice"})

    assert result == "/tmp/home/alice/logs"


def test_expandvars_leaves_unknown_variables_untouched() -> None:
    result = expandvars("$HOME/$MISSING/${ALSO_MISSING}", {"HOME": "/tmp/home"})

    assert result == "/tmp/home/$MISSING/${ALSO_MISSING}"


def test_expandvars_returns_original_string_without_dollar_sign() -> None:
    path = "/var/log/cronico.log"

    assert expandvars(path, {"HOME": "/tmp/home"}) == path


@pytest.mark.parametrize(
    ("cron_cfg", "expected"),
    [
        ("@daily", "0 0 * * *"),
        (" */5 * * * * ", "*/5 * * * *"),
        ({"minute": 0, "hour": 3, "weekday": "1-5"}, "0 3 * * 1-5"),
        ({"minute": "*", "hour": "*", "second": 10}, "* * * * * 10"),
    ],
)
def test_parse_cron_accepts_supported_formats(cron_cfg: str | dict, expected: str) -> None:
    assert parse_cron(cron_cfg) == expected


def test_parse_cron_rejects_unknown_alias() -> None:
    with pytest.raises(ValueError, match="Unknown cron alias"):
        parse_cron("@foobar")


def test_parse_cron_rejects_invalid_type() -> None:
    with pytest.raises(ValueError, match="Invalid cron format"):
        parse_cron(123)  # type: ignore[arg-type]


def test_parse_cron_rejects_invalid_expression() -> None:
    with pytest.raises(ValueError, match="Invalid cron expression"):
        parse_cron("bad cron expr")


def test_extract_shebang_returns_interpreter() -> None:
    command = "\n#!/usr/bin/env python3\nprint('hi')\n"

    assert extract_shebang(command) == "/usr/bin/env python3"


def test_extract_shebang_returns_none_without_shebang() -> None:
    assert extract_shebang("echo hello") is None


def test_extract_script_body_removes_shebang_line() -> None:
    command = "\n#!/usr/bin/env python3\n\nprint('hi')\n"

    assert extract_script_body(command) == "print('hi')"


def test_extract_script_body_strips_leading_whitespace_without_shebang() -> None:
    assert extract_script_body("\n  echo hello\n") == "echo hello\n"
