# Configuration Reference (`.versioning.json`)

`.versioning.json` is **optional**. When omitted, Project Version Manager uses safe defaults. When present, all fields are validated by `scripts/config_loader.py`.

## Schema & Defaults

```json
{
  "schema_version": 1,
  "mode": "auto",
  "strategy": "semver",
  "initial_version": "0.1.0",
  "zero_major_policy": "breaking_to_minor",
  "update_history": "PROJECT_UPDATES.md",
  "changelog": "CHANGELOG.md",
  "version_file": "VERSION",
  "include_documentation_only_changes": false,
  "auto_update_dependent_files": true,
  "require_tests_before_release": true,
  "create_git_tags": false,
  "create_git_commits": false,
  "test_command": null,
  "ignore_paths": [],
  "authoritative_file": null,
  "sync_files": []
}
```

## Field Reference

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `schema_version` | integer | `1` | Must be `1`. |
| `mode` | string | `"auto"` | `"auto"`, `"suggest"`, or `"manual"`. |
| `strategy` | string | `"semver"` | Versioning strategy (`"semver"`). |
| `initial_version` | string | `"0.1.0"` | Initial SemVer used by `init`. |
| `zero_major_policy` | string | `"breaking_to_minor"` | `"breaking_to_minor"` or `"strict_semver"`. |
| `update_history` | string | `"PROJECT_UPDATES.md"` | Relative path to project update history file. |
| `changelog` | string | `"CHANGELOG.md"` | Relative path to changelog file. |
| `version_file` | string | `"VERSION"` | Fallback/generic version filename. |
| `include_documentation_only_changes` | boolean | `false` | Whether doc-only changes warrant release consideration (`analyze` reports `docs_only`). |
| `auto_update_dependent_files` | boolean | `true` | Sync secondary/dependent version declarations automatically. |
| `require_tests_before_release` | boolean | `true` | Enforce test verification (`--run-tests` when `test_command` is set, or warning if unverified). |
| `create_git_tags` | boolean | `false` | Create a local annotated Git tag `vX.Y.Z` during `apply` (same as `--git-tag`; never pushes). |
| `create_git_commits` | boolean | `false` | Create a local Git commit for updated release files during `apply` (same as `--git-commit`; never pushes). |
| `test_command` | string \| null | `null` | Optional shell command executed only when `--run-tests` / `--trust-test-command` is passed. |
| `ignore_paths` | string[] | `[]` | Additional confined relative paths/directories ignored during change detection. |
| `authoritative_file` | string \| null | `null` | Confined relative path to override authoritative source detection. |
| `sync_files` | string[] | `[]` | Confined relative paths of additional version files to keep synchronized. |

> **Security:** All path settings (`update_history`, `changelog`, `version_file`, `authoritative_file`, `ignore_paths`, `sync_files`) must be relative paths resolving inside the project root. Absolute paths and `..` traversal are rejected.

