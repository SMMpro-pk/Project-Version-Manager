# Update History & Changelog Formats

Project Version Manager maintains two complementary files with distinct purposes:

- **`PROJECT_UPDATES.md`**: Detailed internal/AI development memory describing *why* and *how* the project changed, affected areas, compatibility, testing status, and migration notes.
- **`CHANGELOG.md`**: Public, release-level summary formatted according to Keep a Changelog 1.1.0.

## `PROJECT_UPDATES.md` Structure

Newest updates are always prepended directly under `## Project Update History`:

```markdown
### Version 1.5.0 — 2026-11-10

**Update Type:** MINOR

**Category:** New Feature

**Summary:**
Introduced an intelligent project backup module.

**Changes:**
- Added automated backup functionality.
- Introduced backup scheduling.

**Affected Areas:**
- Backend
- Configuration

**Compatibility:** Backward compatible.

**Testing:** Passed.

**Migration Required:** No.

<!-- pvm-fingerprint: <64-hex-sha256> -->

---
```

When recording meaningful work without bumping a release version (`--bump none`), the heading uses `### Unreleased Update (vX.Y.Z) — YYYY-MM-DD` and `CHANGELOG.md` released sections are not modified.

## `CHANGELOG.md` Structure

```markdown
# Changelog

## [Unreleased]

## [1.5.0] - 2026-11-10

### Added
- Automated backup module.
- Backup scheduling functionality.

### Changed
- Improved configuration management.
```

Allowed section headings: `Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`, `Security`.

