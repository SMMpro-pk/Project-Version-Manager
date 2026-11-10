---
name: project-version-manager
description: >-
  Use this skill when the user asks to check, detect, analyze, validate,
  synchronize, initialize, compare, or update a software project's version,
  manage Semantic Versioning (major, minor, patch, pre-release, pre-1.0.0),
  maintain PROJECT_UPDATES.md or CHANGELOG.md, generate release notes, or
  review what changed since the previous release.
license: MIT
metadata:
  version: "1.1.0"
---

# Project Version Manager

Manage software project versions reliably using **Semantic Versioning 2.0.0**, Git-aware change inspection, cross-file version synchronization, and structured release memory (`PROJECT_UPDATES.md` and `CHANGELOG.md`).

> **Separation of Responsibilities**
> - **AI Agent (Semantic Judgment):** Inspect actual code diffs and file contents, distinguish real behavioral impact from trivial edits, classify `major` / `minor` / `patch` / `none`, and write accurate summaries.
> - **CLI Utility (`<skill-dir>/scripts/version_manager.py`):** Perform deterministic version parsing, SemVer calculation, ecosystem detection, `.gitignore`-aware fingerprint deduplication, atomic multi-file updates, and history formatting.

## Core Rules

1. **Never bump blindly on every edit.** Only finalize a version bump for completed, meaningful work. Ignore typos, comment tweaks, formatting-only edits, or internal doc rearrangements unless `.versioning.json` enables `include_documentation_only_changes`.
2. **Never fabricate facts.** Base every summary, change bullet, compatibility note, and test status strictly on observed code, Git history, and tool output.
3. **Respect project configuration and modes:**
   - `auto` (default): Analyze, validate, and apply updates when meaningful changes are ready.
   - `suggest`: Analyze and recommend the next version without modifying files (use `plan` or `apply --dry-run`).
   - `manual`: Only apply a version update when the user explicitly requests a bump category or target version.
4. **Stop on unsafe state.** If version conflicts exist, tests fail, or change impact is genuinely ambiguous, stop and report the exact issue before modifying files.
5. **Prevent duplicate bumps.** Respect the workspace SHA-256 fingerprint check (`already_recorded_for_current_state`); do not release the exact same codebase state twice.
6. **Never auto-execute untrusted repo commands.** If `.versioning.json` defines `test_command`, inspect it first and pass `--run-tests` (`--trust-test-command`) only after verifying it is safe.
7. **Keep skill version separate.** This skill's own version does not govern the target project's version.
8. **No unsolicited Git commits/tags/pushes.** Never create commits, tags, or GitHub releases unless explicitly requested (`--git-commit`, `--git-tag`) or explicitly enabled in `.versioning.json`.

## Step-by-Step Release & Version Workflow

Run the CLI pointing `<skill-dir>` to this skill's directory and `<project-root>` to the target repository:

1. **Identify Project Root & Read Configuration**
   - Locate `<project-root>` and inspect `.versioning.json` if present (see [references/CONFIGURATION.md](./references/CONFIGURATION.md)).
2. **Detect Project Type & Authoritative Version**
   ```bash
   python "<skill-dir>/scripts/version_manager.py" detect "<project-root>" --json
   ```
   - Review detected ecosystems, `authoritative_source`, `version_sources`, and any `conflicts`.
   - If no version exists yet, propose safe initialization (`init "<project-root>" --dry-run`) and confirm before creating files.
   - If only `sync_mismatch` conflicts exist and the user wants to align secondary files without bumping, run `sync "<project-root>" --dry-run` (then `sync "<project-root>"`).
   - If `authoritative_mismatch` conflicts exist, stop and report the conflicting files without silently overwriting them.
3. **Inspect Current Status & History**
   ```bash
   python "<skill-dir>/scripts/version_manager.py" status "<project-root>" --json
   python "<skill-dir>/scripts/version_manager.py" history "<project-root>" --limit 5
   ```
   - Use `PROJECT_UPDATES.md` and `CHANGELOG.md` as project memory (never rely solely on conversation memory).
4. **Inspect Git History & Working-Tree Changes**
   ```bash
   python "<skill-dir>/scripts/version_manager.py" analyze "<project-root>" --json
   ```
   - If `already_recorded_for_current_state` is `true`, report that no new changes exist since the last recorded update and stop.
   - Check `change_classification.docs_only`: if only documentation changed and `include_documentation_only_changes` is `false`, recommend no version bump (`--bump none` only if recording internal notes).
   - If Git is unavailable, acknowledge filesystem-fallback limitations explicitly.
5. **Analyze Actual Code Changes**
   - Read `git diff`, recent commit messages, and modified source files directly. Never classify impact from filenames alone.
6. **Classify Update Impact**
   - Apply [references/SEMVER_POLICY.md](./references/SEMVER_POLICY.md) (`major`, `minor`, `patch`, `prerelease`, or `none`).
7. **Run Relevant Tests & Validate Readiness**
   ```bash
   python "<skill-dir>/scripts/version_manager.py" validate "<project-root>"
   ```
   - Run the project's test suite when applicable. If tests fail, stop and report the failure.
8. **Create a Release Plan**
   ```bash
   python "<skill-dir>/scripts/version_manager.py" plan "<project-root>" \
     --bump minor \
     --category "New Feature" \
     --summary "Concise summary of verified changes" \
     --change "Added X" \
     --area "Backend" \
     --added "Added X" \
     --testing "Passed." \
     --out release-plan.json
   ```
   - Add `--promote-unreleased` if `CHANGELOG.md` has items under `## [Unreleased]` that belong in this release.
9. **Preview or Apply According to Mode**
   - Always preview first when uncertain or in `suggest` mode:
     ```bash
     python "<skill-dir>/scripts/version_manager.py" apply "<project-root>" --plan release-plan.json --dry-run
     ```
   - Apply when authorized (`auto` mode or explicit `manual` request):
     ```bash
     python "<skill-dir>/scripts/version_manager.py" apply "<project-root>" --plan release-plan.json
     ```
   - Pass `--run-tests` if `.versioning.json` defines a verified safe `test_command`. Pass `--git-commit` / `--git-tag` only when explicitly requested.
   - Remove temporary `release-plan.json` if not meant to be committed.
10. **Verify Post-Update State & Report**
    - Confirm `post_validation.valid` is `true`, summarize updated version files, synchronized targets, `PROJECT_UPDATES.md` / `CHANGELOG.md` entries, and any manual lockfile/tag follow-ups.

## Answering Project Memory & Comparison Questions

- *"What is the current version?"* / *"Is this ready for release?"* -> Run `status` and `history --json`.
- *"What changed between versions?"* -> Run `compare "<project-root>" --from <old> --to <new>`.
- *"Generate release notes."* -> Run `release-notes "<project-root>" --version <ver>`.

## References & Assets

- [SemVer & Pre-1.0.0 Policy](./references/SEMVER_POLICY.md)
- [Supported Ecosystems & Manifests](./references/SUPPORTED_PROJECTS.md)
- [Version Detection & Conflict Rules](./references/VERSION_DETECTION.md)
- [Git & Filesystem Change Detection](./references/CHANGE_DETECTION.md)
- [PROJECT_UPDATES.md & CHANGELOG.md Format](./references/UPDATE_HISTORY_FORMAT.md)
- [End-to-End Release Workflow](./references/RELEASE_WORKFLOW.md)
- [Configuration Reference (.versioning.json)](./references/CONFIGURATION.md)
- Templates: [assets/versioning-config.example.json](./assets/versioning-config.example.json), [assets/PROJECT_UPDATES.template.md](./assets/PROJECT_UPDATES.template.md), [assets/CHANGELOG.template.md](./assets/CHANGELOG.template.md)
