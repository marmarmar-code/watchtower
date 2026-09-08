from __future__ import annotations

import argparse
import json
import os
import sys

from .config import load_config
from .engine import RunResult, build_source, run
from .health import inspect_health, render_health
from .models import NotificationEntry
from .notifier import build_notifier
from .rss_profiles import load_profiles
from .runtime_safety import validate_runtime
from .source_catalog import load_catalog
from .state import StateStore
from .setup import PRESETS, setup
from . import __version__
from .github_setup import link_github


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="watchtower")
    p.add_argument("--version", action="version", version=f"Watchtower {__version__}; config=1")
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
    link = sub.add_parser("link-github", help="kontroller og koble egne GitHub-repoer")
    link.add_argument("--code-repo", required=True)
    link.add_argument("--runtime-repo", required=True)
    link.add_argument("--runtime-ref", default="main")
    link.add_argument("--apply", action="store_true")
    setup_cmd = sub.add_parser("setup", help="lag et privat oppsett med en startpakke")
    setup_cmd.add_argument("--runtime", required=True)
    setup_cmd.add_argument("--preset", choices=tuple(PRESETS))
    setup_cmd.add_argument("--topic", action="append", default=[])
    setup_cmd.add_argument("--company", action="append", default=[], metavar="ORGNR=NAME")
    setup_cmd.add_argument("--channel", choices=("teams", "slack"), default="teams")
    validate = sub.add_parser("validate-runtime")
    validate.add_argument("path")
    validate_config = sub.add_parser("validate-config")
    validate_config.add_argument("--config", required=True)
    sub.add_parser("list-sources")
    sub.add_parser("list-rss-profiles")
    history = sub.add_parser("history", help="les privat varselhistorikk lokalt")
    history.add_argument("--state-dir", required=True)
    history.add_argument("--latest", action="store_true", help="vis hele siste varselrunde")
    history.add_argument("--limit", type=int, default=20)
    history.add_argument("--redact-output", action="store_true")
    status = sub.add_parser("status")
    status.add_argument("--config", required=True)
    status.add_argument("--state-dir", required=True)
    status.add_argument("--redact-output", action="store_true")
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
    if args.command == "link-github":
        return link_github(args)
    if args.command == "setup":
        try:
            return setup(args)
        except (OSError, ValueError, EOFError) as exc:
            print(f"Oppsett kunne ikke fullføres: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
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
    if args.command == "list-rss-profiles":
        print("ID\tSTATUS\tKONTROLLERT\tEIER\tNAVN")
        status_labels = {"verified": "klar", "directory": "velg feed"}
        for profile in sorted(load_profiles(), key=lambda row: str(row["id"])):
            print(
                f"{profile['id']}\t{status_labels[profile['status']]}\t"
                f"{profile['verified_on']}\t"
                f"{profile['owner']}\t{profile['name']}"
            )
        return 0
    if args.command == "history":
        audit = StateStore(args.state_dir).load("_latest_alerts" if args.latest else "_alert_audit") or {}
        entries = audit.get("entries", [])
        if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
            print("Invalid private alert history", file=sys.stderr)
            return 1
        if args.limit < 1:
            print("history --limit must be positive", file=sys.stderr)
            return 1
        if args.redact_output:
            print(f"WATCHTOWER HISTORY; entries={len(entries)}")
            return 0
        selected = entries if args.latest else entries[-args.limit:]
        for entry in selected:
            print(json.dumps(entry, ensure_ascii=False))
        return 0
    if args.command == "status":
        config = load_config(args.config)
        report = inspect_health(config, StateStore(args.state_dir))
        print(render_health(report, redacted=args.redact_output))
        return 0 if report.okay else 2
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
            f"baselines={result.baselined_sources} alerts={result.alerts} errors={len(result.errors)} "
            f"coverage_limited={len(result.warnings)}"
        )
    else:
        print(result)
    return result_exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
