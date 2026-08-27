# Changelog

## Alert-storm hotfix — 2026-08-27

### Fixed

- Notification-only details no longer change persisted item fingerprints.
- State written by the short-lived transition fingerprint is accepted and silently migrated.
- More than 32 alerts in one run are replaced by one safety summary instead of detailed notification batches.

## Distribution-ready baseline — 2026-08-26

### Added

- Microsoft Teams notifications through Adaptive Cards.
- Provider-specific Slack and Teams formatting.
- Bounded notification batches.
- Optional BRREG monitoring for annual accounts, company status and roles.
- Fork-local private runtime discovery with optional repository variables.
- Runtime preflight validation and representative notification tests.
- Parser contract tests for public source adapters.
- Generic setup, support, contribution and security documentation.

### Changed

- All third-party GitHub Actions are pinned to reviewed commit SHAs.
- Enabled sources must have complete configuration and positive filter rules.
- Configured company identifiers are included in private/public leak checks.
- Setup placeholders are excluded from leak deny-lists until they are replaced.
- Monitoring runs fail when no source is enabled.
- CI includes patch, package, compilation, security, unit and CLI checks.

### Fixed

- BRREG state changes no longer generate repeat alerts on the following unchanged run.
- BRREG organisation numbers are validated before monitoring starts.
- Missing BRREG entities fail closed instead of becoming apparent changes.
- Removed BRREG entities produce a focused status alert without synthetic form or industry changes.
- Teams alerts no longer contain Slack link syntax.
- Teams and Power Automate webhook URLs are detected as secret-like content.

### Pending before general open-source distribution

- Confirm the correct rights holder and add an explicit software license.
