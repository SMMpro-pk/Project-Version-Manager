# Semantic Versioning Policy

Project Version Manager follows **Semantic Versioning 2.0.0** (`MAJOR.MINOR.PATCH[-PRERELEASE][+BUILD]`).

## Bump Classification Criteria

### 1. Major (`X.0.0`)
Increase **MAJOR** for incompatible or breaking changes (when `MAJOR >= 1`):
- Breaking public API, CLI, or schema changes.
- Removing existing features, endpoints, or configuration options.
- Major database migrations or incompatible storage format changes.
- Complete architectural redesign or platform migration that breaks compatibility.

### 2. Minor (`X.Y.0`)
Increase **MINOR** for backward-compatible new capabilities:
- New user-facing features, modules, plugins, or integrations.
- New API endpoints, CLI subcommands, or optional configuration keys.
- Substantial non-breaking enhancements to existing functionality.

### 3. Patch (`X.Y.Z`)
Increase **PATCH** for backward-compatible fixes:
- Bug fixes, crash fixes, and error-handling improvements.
- Security patches and dependency vulnerability remediations.
- Performance optimizations and small UI/UX corrections.

### 4. No Version Bump (`none`)
Do **not** bump the version for:
- Typo fixes, comment updates, or whitespace/formatting changes.
- Internal documentation reorganization (unless `include_documentation_only_changes: true`).
- Test-only updates that do not alter production behavior.
- Unfinished or work-in-progress edits.

Use `--bump none` when you want to record meaningful internal work in `PROJECT_UPDATES.md` as an `Unreleased Update` without creating a release version.

## Pre-1.0.0 Policy (`0.Y.Z`)

Projects below `1.0.0` are controlled by `zero_major_policy` in `.versioning.json`:

| Policy | Breaking Change (`major`) | New Feature (`minor`) | Bug Fix (`patch`) |
| --- | --- | --- | --- |
| `breaking_to_minor` *(default)* | `0.(Y+1).0` | `0.(Y+1).0` | `0.Y.(Z+1)` |
| `strict_semver` | `1.0.0` | `0.(Y+1).0` | `0.Y.(Z+1)` |

To promote a `0.x` project to `1.0.0` deliberately, use `--target-version 1.0.0`.

## Pre-release & Build Metadata

- **Starting a pre-release:** `1.2.0` + `--bump minor --pre-id alpha` -> `1.3.0-alpha.1`
- **Incrementing a pre-release:** `1.3.0-alpha.1` + `--bump prerelease` -> `1.3.0-alpha.2`
- **Advancing pre-release stage:** `1.3.0-alpha.2` + `--bump prerelease --pre-id beta` -> `1.3.0-beta.1`
- **Graduating to stable:** `1.3.0-rc.1` + `--bump patch` (or `--bump minor` when `patch == 0`) -> `1.3.0`
- **Build metadata:** `--build build.105` appends `+build.105` (ignored in SemVer precedence comparisons per SemVer 2.0.0 §10–11).

