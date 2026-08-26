# Changelog

## Unreleased distribution candidate

### Added

- Microsoft Teams notifications through Adaptive Cards.
- Provider-specific Slack and Teams formatting.
- Bounded notification batches.
- Optional BRREG monitoring for annual accounts, company status and roles.
- Fork-local private runtime discovery with optional repository variables.
- Runtime preflight validation and representative notification tests.
- Parser contract tests for public source adapters.

### Changed

- All third-party GitHub Actions are pinned to reviewed commit SHAs.
- Enabled sources must have complete configuration and positive filter rules.
- Configured company identifiers are included in private/public leak checks.
- CI now includes patch, package, compilation, security, unit and CLI checks.

### Fixed

- BRREG state changes no longer generate repeat alerts on the following unchanged run.
- BRREG organisation numbers are validated before monitoring starts.
- Missing BRREG entities fail closed instead of becoming apparent changes.
- Teams alerts no longer contain Slack link syntax.
- Teams and Power Automate webhook URLs are detected as secret-like content.
