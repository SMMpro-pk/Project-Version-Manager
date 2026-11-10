# Git & Filesystem Change Detection

## What `analyze` Inspects

When Git is available and the project is inside a Git worktree, `scripts/git_changes.py` collects:
- Current branch and `HEAD` commit (`sha`, `subject`, `date`).
- Latest and previous Semantic Versioning tags (`vX.Y.Z` or `X.Y.Z`).
- Commits since the latest SemVer tag (up to 100 recent commits).
- Files changed between the latest SemVer tag and `HEAD`.
- Working-tree status (`git status --porcelain=v1 -z -uall`): staged, unstaged, untracked, added, modified, deleted, and renamed paths.
- Diff statistics (`git diff --stat`, `git diff --cached --stat`).

Ignored directories (`.git`, `node_modules`, `__pycache__`, `.venv`, `dist`, `build`, `target`, `vendor`, etc., plus `.versioning.json` `ignore_paths`) are excluded automatically.

## Filesystem Fallback (No Git)

If Git is not installed or the directory is not a Git repository:
- `git_available` is `false`, and `limitations` explains why Git history is unavailable.
- Workspace fingerprinting still works by hashing all non-ignored project files so duplicate updates are still prevented.

## Duplicate Update Prevention (Fingerprinting)

To prevent counting the same completed work twice:
1. The CLI computes a SHA-256 fingerprint over all non-ignored files in the project, excluding `PROJECT_UPDATES.md`, `CHANGELOG.md`, `.versioning-state.json`, and `release-plan.json`.
2. When `apply` completes, the post-update fingerprint is saved to `.versioning-state.json` and embedded as `<!-- pvm-fingerprint: <sha256> -->` in `PROJECT_UPDATES.md`.
3. Subsequent `analyze` or `apply` runs compare the current fingerprint against the recorded fingerprint. If they match, `already_recorded_for_current_state` is `true` and `apply` stops safely (unless `--force-duplicate` is explicitly passed).

