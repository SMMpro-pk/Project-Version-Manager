# Handling Multi-File Version Conflicts

If a project contains conflicting version declarations, for example:
- `pyproject.toml` declares `1.4.0` (authoritative)
- `src/app/__init__.py` declares `1.3.2` (dependent)

Running `detect` or `validate` reports:

```text
Conflicts:
  ! src/app/__init__.py (dependent) has version '1.3.2', which differs from authoritative version '1.4.0'.
```

`plan` and `apply` will refuse to overwrite files until the conflict is resolved or an explicit `authoritative_file` is confirmed and synced safely.

