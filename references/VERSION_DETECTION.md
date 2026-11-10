# Version Detection & Conflict Resolution

## Source Roles

Every discovered version declaration is assigned a role:

1. **`authoritative`**: The canonical source of truth for the project's version (e.g., `package.json`, `pyproject.toml`, `Cargo.toml`, `composer.json`, `pubspec.yaml`, `pom.xml`, `Directory.Build.props`, WordPress plugin/theme header, or `VERSION` when no package manifest defines a version).
2. **`dependent`**: Secondary declarations that should stay synchronized with the authoritative version (e.g., `src/<pkg>/__init__.py` `__version__`, WordPress `readme.txt` `Stable tag:`, `*.csproj` when `Directory.Build.props` exists, or `VERSION` alongside `package.json`).
3. **`lockfile`**: Generated lockfile root metadata (`package-lock.json` root/packages[""], `Cargo.lock` root crate entry).

You can explicitly override which file is authoritative in `.versioning.json` using `"authoritative_file": "path/to/file"`.

## Conflict Handling Rules

- **Authoritative mismatch:** If two authoritative files report different versions (for example, `pyproject.toml` says `1.2.0` and `package.json` says `1.3.0`), `detect` and `validate` report an `authoritative_mismatch` conflict and `apply`/`plan` refuse to proceed until resolved.
- **Sync mismatch:** If a `dependent` or `lockfile` source differs from the authoritative version (for example, WordPress `plugin.php` is `1.4.0` while `readme.txt` `Stable tag:` is `1.3.0`), a `sync_mismatch` conflict is reported.
- **Non-SemVer declarations:** If any detected version fails SemVer 2.0.0 validation, `validate` fails and explains which file contains the invalid version.

## Missing Version Initialization

When no version declaration is detected:
1. Run `python scripts/version_manager.py init "<project-root>" --dry-run`.
2. Confirm with the user (or use configured `initial_version`, default `0.1.0`) before creating `VERSION`, `PROJECT_UPDATES.md`, and `CHANGELOG.md`.
3. Never silently overwrite existing manifests.

