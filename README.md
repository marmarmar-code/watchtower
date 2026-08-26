# watchtower

Deterministic monitoring engine for public data sources.

## Security model

This repository is intentionally public. It contains the monitoring engine, source adapters, tests and workflow logic, but **no production watchlists, search terms, priorities, runtime state or secret values**.

Production uses a separate private runtime repository for configuration and state. GitHub Actions checks out that runtime with a repository-scoped credential, masks private configuration before execution and commits only runtime state back to the private repository.

Filtering is deterministic. No AI/LLM is required at runtime.

## Notifications

Watchtower supports Slack and Microsoft Teams.

The private runtime can select the provider:

```toml
[notifications]
provider = "slack"
```

or:

```toml
[notifications]
provider = "teams"
```

If `[notifications]` is omitted, Watchtower defaults to Slack for backward compatibility.

Slack uses the GitHub Actions secret `SLACK_WEBHOOK_URL`.

Microsoft Teams uses `TEAMS_WEBHOOK_URL` and expects a Teams Workflows webhook. Watchtower sends Teams notifications as Adaptive Cards.

Notification endpoints are secrets and must never be committed to either the public repository or a private runtime repository.

## Secrets

Credentials and notification endpoints are supplied through GitHub Actions secrets and must never be committed to this repository.

## Local validation

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
python scripts/check_public_safety.py
python -m unittest discover -s tests -v
```

## Runtime contract

A private runtime provides configuration plus persisted state. The monitor establishes a silent baseline for a newly enabled source, then compares later runs against that state and emits notifications only for items that satisfy the private rules.
