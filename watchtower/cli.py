from __future__ import annotations

import argparse
import os

from .config import load_config
from .engine import RunResult, run
from .notifier import build_notifier
from .runtime_safety import validate_runtime
from .state import StateStore


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="watchtower")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("run", "dry-run"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--config", required=True)
        cmd.add_argument("--state-dir", required=True)
        cmd.add_argument("--redact-output", action="store_true")
    validate = sub.add_parser("validate-runtime")
    validate.add_argument("path")
    test_notification = sub.add_parser("test-notification")
    test_notification.add_argument("--config", required=True)
    sub.add_parser("test-slack")
    return p


def result_exit_code(result: RunResult) -> int:
    return 2 if result.errors else 0


def _notifier_for_config(config):
    return build_notifier(
        config.notifications.provider,
        slack_url=os.environ.get("SLACK_WEBHOOK_URL", ""),
        teams_url=os.environ.get("TEAMS_WEBHOOK_URL", ""),
    )


def main() -> int:
    args = parser().parse_args()
    if args.command == "validate-runtime":
        problems = validate_runtime(args.path)
        if problems:
            for problem in problems:
                print(problem)
            return 1
        print("PRIVATE RUNTIME SAFETY OK")
        return 0
    if args.command == "test-slack":
        build_notifier("slack", slack_url=os.environ.get("SLACK_WEBHOOK_URL", "")).send(
            "Watchtower: Slack-varsling er koblet til og fungerer."
        )
        return 0
    if args.command == "test-notification":
        config = load_config(args.config)
        provider = config.notifications.provider
        _notifier_for_config(config).send(
            f"Watchtower: {provider}-varsling er koblet til og fungerer."
        )
        return 0

    config = load_config(args.config)
    state = StateStore(args.state_dir)
    dry_run = args.command == "dry-run"
    notifier = None if dry_run else _notifier_for_config(config)
    result = run(config, state, notifier, dry_run=dry_run)
    if args.redact_output:
        print(
            f"watchtower complete; sources={result.checked_sources} "
            f"baselines={result.baselined_sources} alerts={result.alerts} errors={len(result.errors)}"
        )
    else:
        print(result)
    return result_exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
