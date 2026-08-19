# Security

This public repository must remain safe to publish at all times.

Forbidden in Git history:

- production keywords/watchlists or editorial priorities
- private runtime state or event history
- Slack webhook URLs
- deploy keys, tokens, PEM/JWK/certificates
- copied private runtime configuration
- production output containing monitored identities solely because they are monitored

The production workflow must:

1. keep public `GITHUB_TOKEN` permissions read-only;
2. access the private runtime only with a repository-scoped credential;
3. mask private config values before running monitors;
4. never print private configuration;
5. commit only `state/` in the private runtime;
6. fail closed if unexpected private-runtime files are staged.
