# watchtower

Public source code for Medier24-style deterministic monitoring of public sources.

## Security model

This repository is intentionally public and contains **no production watchlists, keywords, priorities, event history, Slack webhook or runtime state**.

Production runs use a separate private repository, expected at `marmarmar-code/watchtower-runtime`, which contains only private configuration and state. GitHub Actions checks out the private runtime using a repository-scoped deploy key and commits **only `state/`** back to it.

Supported source adapters in V1:

- Regjeringen.no RSS
- Stortingets åpne data: saker, skriftlige spørsmål and høringer
- Konkurransetilsynet: fusjoner og oppkjøp
- Euronext issuer news (small experimental adapter)
- Doffin slot/config contract; disabled until a verified data interface is wired in

Filtering is deterministic. No AI/LLM is used.

## Production secrets

The public repo needs two GitHub Actions secrets:

- `RUNTIME_DEPLOY_KEY`: write-enabled deploy key for the private runtime repo
- `SLACK_WEBHOOK_URL`: Slack incoming webhook

Never commit either secret.

## Local validation

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
python scripts/check_public_safety.py
python -m unittest discover -s tests -v
```

## Runtime contract

The private repo contains:

```text
config/watchtower.toml
state/*.json
```

The monitor baselines each source silently on first run. Afterwards, new or materially changed items that match private rules are sent to Slack. State is advanced only after Slack succeeds when alerts are pending.
