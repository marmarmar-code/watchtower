from __future__ import annotations

import argparse
import os
import sys

from .config import load_config
from .engine import RunResult, build_source, run
from .models import NotificationEntry
from .notifier import build_notifier
from .runtime_safety import validate_runtime
from .source_catalog import load_catalog
from .state import StateStore


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="watchtower")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("run", "dry-run"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--config", required=True)
        cmd.add_argument("--state-dir", required=True)
        cmd.add_argument("--redact-output", action="store_true")
        cmd.add_argument(
            "--respect-intervals",
            action="store_true",
            help="only poll sources whose configured interval has elapsed",
        )
    validate = sub.add_parser("validate-runtime")
    validate.add_argument("path")
    validate_config = sub.add_parser("validate-config")
    validate_config.add_argument("--config", required=True)
    sub.add_parser("list-sources")
    test_notification = sub.add_parser("test-notification")
    test_notification.add_argument("--config", required=True)
    sub.add_parser("test-slack")
    sub.add_parser("test-teams")
    return p


def result_exit_code(result: RunResult) -> int:
    return 2 if result.errors else 0


def _notifier_for_config(config):
    return build_notifier(
        config.notifications.provider,
        slack_url=os.environ.get("SLACK_WEBHOOK_URL", ""),
        teams_url=os.environ.get("TEAMS_WEBHOOK_URL", ""),
    )


def _sample_entry(provider: str) -> NotificationEntry:
    return NotificationEntry(
        source_label="Varslingstest",
        status="TEST",
        title=f"Watchtower er koblet til {provider}",
        url="https://example.com/",
        published="Eksempel",
        matched_terms=("test",),
        details=("Dette er et representativt testvarsel.",),
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
    if args.command == "validate-config":
        config = load_config(args.config)
        for source in config.sources:
            if source.enabled:
                build_source(source)
        enabled = sum(1 for source in config.sources if source.enabled)
        print(f"WATCHTOWER CONFIG OK; enabled_sources={enabled}")
        return 0
    if args.command == "list-sources":
        print("ID\tSTATUS\tTILGANG\tVEDLIKEHOLD\tNAVN")
        status_labels = {
            "stable": "etablert",
            "beta": "prøveversjon",
            "maintenance": "vedlikehold",
            "deprecated": "utfases",
        }
        for source in sorted(load_catalog(), key=lambda row: str(row["id"])):
            access = "krever nøkkel" if source["credential_required"] else "offentlig"
            owner = (
                "egen fork"
                if source["maintenance_owner"] == "fork-owner"
                else source["maintenance_owner"]
            )
            print(
                f"{source['id']}\t{status_labels[source['status']]}\t{access}\t"
                f"{owner}\t{source['name']}"
            )
        return 0
    if args.command == "test-slack":
        notifier = build_notifier(
            "slack",
            slack_url=os.environ.get("SLACK_WEBHOOK_URL", ""),
        )
        notifier.send_alerts((_sample_entry("Slack"),))
        return 0
    if args.command == "test-teams":
        notifier = build_notifier(
            "teams",
            teams_url=os.environ.get("TEAMS_WEBHOOK_URL", ""),
        )
        notifier.send_alerts((_sample_entry("Microsoft Teams"),))
        return 0
    if args.command == "test-notification":
        config = load_config(args.config)
        provider = config.notifications.provider
        _notifier_for_config(config).send_alerts((_sample_entry(provider),))
        return 0

    config = load_config(args.config)
    if not any(source.enabled for source in config.sources):
        print("Watchtower requires at least one enabled source", file=sys.stderr)
        return 1

    state = StateStore(args.state_dir)
    dry_run = args.command == "dry-run"
    notifier = None if dry_run else _notifier_for_config(config)
    result = run(
        config,
        state,
        notifier,
        dry_run=dry_run,
        respect_intervals=args.respect_intervals,
    )
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
