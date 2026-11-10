# Project Update History

This file maintains a chronological record of meaningful project updates for developers and AI agents.

## Project Update History

### Version 1.1.0 — 2026-11-10

**Update Type:** MINOR

**Category:** Security, Feature Completion & Ecosystem Expansion

**Summary:**
Hardened repository security, implemented sync and compare commands, added .gitignore-aware fingerprinting, expanded ecosystem support, and upgraded CI and tests.

**Changes:**
- Added strict path confinement validation for .versioning.json paths and explicit trust gating (--run-tests) for repository test_command execution.
- Implemented sync and compare CLI subcommands, --promote-unreleased changelog promotion, and opt-in --git-commit / --git-tag support.
- Added WordPress PHP constant sync, real custom sync_files detection, iOS (Info.plist, podspec, pbxproj), nested .csproj, Cargo workspace, and Flit/Hatch support.
- Switched Git workspace fingerprinting to .gitignore-aware git ls-files, scoped SemVer tags to HEAD ancestry, fixed target-version bump inference, and standardized package imports across scripts/.

**Affected Areas:**
- Security
- CLI & Core Engine
- Ecosystems
- CI & Tests

**Compatibility:** Backward compatible.

**Testing:** Passed.

**Migration Required:** No.

<!-- pvm-fingerprint: 635b1ef14f47dbc918390c2024caccf41ca79857692590686aa3d95c9d3fc4d9 -->

---
