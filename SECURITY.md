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
5. derive the public leak deny-list automatically from private `protected_values`, filter rules (`include_any`, `include_all`, `exclude_any`) and `search_queries`;
6. check that none of those private runtime terms occur in the public source tree before monitoring;
7. commit only `state/` in the private runtime;
8. fail closed if unexpected private-runtime files are staged.

Leak checks must report only affected public file paths, never the private values themselves.
