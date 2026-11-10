# Security Policy

## Supported Versions

| Version | Supported |
| ------- | --------- |
| 1.0.x   | Yes       |

## Security Design Principles

Project Version Manager is designed with safety and least privilege in mind:

- **No network execution or remote script downloading:** The CLI operates strictly locally on repository files and local Git metadata.
- **No credential storage:** Never stores tokens, API keys, or authentication secrets in `.versioning.json`, `PROJECT_UPDATES.md`, `CHANGELOG.md`, or source files.
- **No destructive Git operations by default:** Never executes `git push --force`, `git reset --hard`, or `git clean`, and disables automatic `git commit` / `git tag` creation by default.
- **No blind global search-and-replace:** Version updates target only verified project manifest fields and explicitly configured sync targets.
- **Atomic rollback on write failure:** Multi-file updates restore original file contents if any write or post-update validation step fails.
- **Input validation:** Configuration files (`.versioning.json`) and version strings are validated before any file modification occurs.

## Reporting a Vulnerability

If you discover a security issue (for example, path traversal in file synchronization, unsafe command execution, or unintended file modification):

1. Do **not** open a public issue with exploit details.
2. Report the finding privately to the repository maintainers via GitHub Security Advisories (if enabled on the repository) or direct maintainer contact.
3. Include steps to reproduce, affected files, and expected vs. actual behavior.

