# Changelog

All notable changes to the **Project Version Manager** skill itself are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> Note: This changelog tracks releases of the `project-version-manager` Agent Skill repository (`1.0.0`), which is independent of the version histories of projects managed by the skill.

## [Unreleased]

## [1.0.0] - 2026-11-10

### Added
- Agent Skill entrypoint (`SKILL.md`) following the open Agent Skills specification with clear activation triggers and step-by-step release management instructions.
- Deterministic Python CLI (`scripts/version_manager.py`) with `detect`, `status`, `analyze`, `validate`, `plan`, `apply`, `init`, `history`, and `release-notes` subcommands.
- Full Semantic Versioning 2.0.0 parser, validator, comparator, prerelease/build-metadata support, and configurable pre-1.0.0 policy (`scripts/semver_utils.py`).
- Multi-ecosystem project and version detector (`scripts/detect_version.py`) covering JavaScript/TypeScript, Python, Rust, PHP/Composer, Java/Gradle/Maven, .NET/C#, Dart/Flutter, Go, C/C++, iOS/Android, WordPress plugins/themes, and generic `VERSION` files.
- Git-based change inspection with filesystem fallback and SHA-256 change fingerprinting (`scripts/git_changes.py`) to prevent duplicate version bumps.
- Format-preserving multi-file version synchronization with atomic rollback (`scripts/sync_versions.py`).
- Chronological `PROJECT_UPDATES.md` and `CHANGELOG.md` management (`scripts/history_manager.py`).
- Configuration loader and schema validator for `.versioning.json` (`scripts/config_loader.py`).
- Technical reference guides in `references/`, starter templates in `assets/`, realistic examples in `examples/`, automated `unittest` suite (`scripts/test_version_manager.py`), and GitHub Actions CI workflow.

