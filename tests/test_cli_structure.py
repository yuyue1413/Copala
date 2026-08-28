"""Stable CLI command-tree tests after splitting Typer groups."""

from typer.testing import CliRunner

from copala.cli.commands import app

runner = CliRunner()


def test_root_command_tree_is_stable():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in (
        "onboard",
        "gateway",
        "agent",
        "status",
        "memory",
        "calendar",
        "life-events",
        "smart-reminders",
        "channels",
        "provider",
    ):
        assert command in result.stdout


def test_split_group_command_names_are_stable():
    expectations = {
        "memory": ("reindex", "search", "list"),
        "calendar": (
            "ingest",
            "list",
            "calendars",
            "status",
            "feishu-auth-url",
            "feishu-auth-code",
            "feishu-auth-login",
        ),
        "life-events": ("list", "reprocess-email"),
        "smart-reminders": ("status", "check", "patrol"),
        "channels": ("status", "login"),
        "provider": ("login",),
    }
    for group, commands in expectations.items():
        result = runner.invoke(app, [group, "--help"])
        assert result.exit_code == 0
        for command in commands:
            assert command in result.stdout
