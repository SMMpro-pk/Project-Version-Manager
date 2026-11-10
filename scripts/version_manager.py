#!/usr/bin/env python3
"""Unified CLI for Project Version Manager."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure sibling imports work regardless of current working directory
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from config_loader import CONFIG_FILENAME, DEFAULT_CONFIG, load_config
from detect_version import detect_project
from git_changes import (
    compute_workspace_fingerprint,
    inspect_changes,
    save_recorded_state,
)
from history_manager import (
    CHANGELOG_HEADER,
    UPDATES_HEADER,
    format_changelog_entry,
    format_project_update_entry,
    generate_release_notes,
    parse_changelog_versions,
    parse_project_updates,
    prepare_changelog_content,
    prepare_project_updates_content,
)
from semver_utils import SemVerError, calculate_next_version, is_valid_semver, parse_semver
from sync_versions import apply_atomic_writes, plan_version_sync
from validate_version import validate_project


DEFAULT_CATEGORY_MAP = {
    "major": "Breaking Change",
    "minor": "New Feature",
    "patch": "Bug Fix",
    "prerelease": "Pre-release",
    "none": "Maintenance",
}


def _resolve_root(path_arg: Optional[str]) -> Path:
    return Path(path_arg or ".").resolve()


def _run_configured_tests(root: Path, command: str) -> Dict[str, Any]:
    try:
        proc = subprocess.run(
            command,
            cwd=str(root),
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            check=False,
        )
        return {
            "executed": True,
            "passed": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout[-2000:],
            "stderr": proc.stderr[-2000:],
        }
    except Exception as exc:
        return {
            "executed": True,
            "passed": False,
            "returncode": -1,
            "stdout": "",
            "stderr": str(exc),
        }


def build_status(root: Path) -> Dict[str, Any]:
    detection = detect_project(root)
    config = detection["config"]
    changes = inspect_changes(
        root,
        update_history_rel=config["update_history"],
        changelog_rel=config["changelog"],
        extra_ignores=config["ignore_paths"],
    )
    validation = validate_project(root)
    updates = parse_project_updates(root / config["update_history"])
    changelog_sections = parse_changelog_versions(root / config["changelog"])

    previous_version = None
    latest_update = updates[0] if updates else None
    if len(updates) >= 2:
        previous_version = updates[1].get("version")
    elif len(updates) == 1 and updates[0].get("version") != detection["current_version"]:
        previous_version = updates[0].get("version")
    elif changes.get("previous_semver_tag"):
        previous_version = changes["previous_semver_tag"]

    unreleased_section = next(
        (s for s in changelog_sections if s["version"].lower() == "unreleased"), None
    )

    ready_for_release = (
        validation["valid"]
        and detection["current_version"] is not None
        and not changes["already_recorded_for_current_state"]
    )

    return {
        "project_root": str(root),
        "mode": config["mode"],
        "ecosystems": detection["ecosystems"],
        "current_version": detection["current_version"],
        "previous_version": previous_version,
        "authoritative_source": detection["authoritative_source"],
        "version_sources": detection["version_sources"],
        "conflicts": detection["conflicts"],
        "git_available": changes["git_available"],
        "branch": changes["branch"],
        "latest_semver_tag": changes["latest_semver_tag"],
        "commits_since_tag": changes["total_commits_since_tag"],
        "working_tree_clean": changes["working_tree"]["clean"],
        "already_recorded_for_current_state": changes["already_recorded_for_current_state"],
        "latest_update": latest_update,
        "unreleased_changelog_notes": unreleased_section["body"] if unreleased_section else "",
        "validation_passed": validation["valid"],
        "validation_errors": validation["errors"],
        "ready_for_release": ready_for_release,
    }


def build_plan(
    root: Path,
    *,
    bump: str,
    pre_id: Optional[str] = None,
    build: Optional[str] = None,
    target_version: Optional[str] = None,
    allow_downgrade: bool = False,
    category: Optional[str] = None,
    summary: Optional[str] = None,
    changes_list: Optional[List[str]] = None,
    affected_areas: Optional[List[str]] = None,
    changelog_sections: Optional[Dict[str, List[str]]] = None,
    compatibility: str = "Backward compatible.",
    testing: str = "Not run.",
    migration_required: str = "No.",
    entry_date: Optional[str] = None,
) -> Dict[str, Any]:
    detection = detect_project(root)
    config = detection["config"]

    if detection["config_errors"]:
        raise ValueError("; ".join(detection["config_errors"]))
    if detection["conflicts"]:
        raise ValueError(
            "Version conflicts detected; resolve before planning: "
            + "; ".join(c["message"] for c in detection["conflicts"])
        )
    if detection["current_version"] is None:
        raise ValueError(
            "No authoritative version detected. Run 'init' first to initialize project versioning."
        )

    current_sv = parse_semver(detection["current_version"])
    next_sv = calculate_next_version(
        current_sv,
        bump,
        zero_major_policy=config["zero_major_policy"],
        pre_id=pre_id,
        build=build,
        target_version=target_version,
        allow_downgrade=allow_downgrade,
    )

    bump_norm = bump.strip().lower()
    next_version_str = str(next_sv)
    git_info = inspect_changes(
        root,
        update_history_rel=config["update_history"],
        changelog_rel=config["changelog"],
        extra_ignores=config["ignore_paths"],
    )

    if bump_norm == "none":
        file_edits: List[Dict[str, Any]] = []
    else:
        planned_edits, _ = plan_version_sync(
            root,
            detection["version_sources"],
            next_version_str,
            auto_update_dependent=config["auto_update_dependent_files"],
            extra_sync_files=config["sync_files"],
        )
        file_edits = [e.to_dict() for e in planned_edits]

    resolved_category = category or DEFAULT_CATEGORY_MAP.get(bump_norm, "Update")
    resolved_summary = (summary or "").strip()
    if not resolved_summary:
        if bump_norm == "none":
            resolved_summary = "Recorded non-release project update notes."
        else:
            resolved_summary = f"Version {next_version_str} ({resolved_category.lower()})."

    resolved_changes = [c.strip() for c in (changes_list or []) if c and c.strip()]
    if not resolved_changes:
        if git_info["commits_since_tag"]:
            resolved_changes = [c["subject"] for c in git_info["commits_since_tag"][:10]]
        else:
            resolved_changes = [resolved_summary]

    resolved_sections: Dict[str, List[str]] = {}
    if changelog_sections:
        for k, vals in changelog_sections.items():
            cleaned = [v.strip() for v in vals if v and v.strip()]
            if cleaned:
                resolved_sections[k] = cleaned
    if not resolved_sections and bump_norm != "none":
        default_sec = "Fixed" if bump_norm == "patch" else ("Removed" if "breaking" in resolved_category.lower() else "Added" if bump_norm == "minor" else "Changed")
        resolved_sections[default_sec] = resolved_changes

    return {
        "schema_version": 1,
        "project_root": str(root),
        "mode": config["mode"],
        "current_version": str(current_sv),
        "next_version": next_version_str,
        "bump": bump_norm,
        "category": resolved_category,
        "summary": resolved_summary,
        "changes": resolved_changes,
        "affected_areas": [a.strip() for a in (affected_areas or []) if a and a.strip()],
        "changelog_sections": resolved_sections,
        "compatibility": compatibility,
        "testing": testing,
        "migration_required": migration_required,
        "date": entry_date or date.today().isoformat(),
        "already_recorded_for_current_state": git_info["already_recorded_for_current_state"],
        "current_fingerprint": git_info["fingerprint"],
        "file_edits": file_edits,
    }


def apply_plan(
    root: Path,
    plan: Dict[str, Any],
    *,
    dry_run: bool = False,
    force_duplicate: bool = False,
    run_tests: bool = False,
    skip_tests: bool = False,
    explicit_user_request: bool = False,
) -> Dict[str, Any]:
    detection = detect_project(root)
    config = detection["config"]

    if detection["config_errors"]:
        raise ValueError("; ".join(detection["config_errors"]))

    mode = config["mode"]
    if mode == "suggest" and not dry_run:
        raise ValueError(
            "Project mode is 'suggest'. File modifications are disabled; "
            "use --dry-run or 'plan' to preview recommendations."
        )
    if mode == "manual" and not explicit_user_request and not dry_run:
        raise ValueError(
            "Project mode is 'manual'. Provide an explicit --plan or explicit --bump/--target-version."
        )

    if detection["conflicts"]:
        raise ValueError(
            "Cannot apply release while version conflicts exist: "
            + "; ".join(c["message"] for c in detection["conflicts"])
        )

    if detection["current_version"] != plan["current_version"]:
        raise ValueError(
            f"Plan expects current version '{plan['current_version']}', "
            f"but project is currently at '{detection['current_version']}'."
        )

    git_info = inspect_changes(
        root,
        update_history_rel=config["update_history"],
        changelog_rel=config["changelog"],
        extra_ignores=config["ignore_paths"],
    )
    if git_info["already_recorded_for_current_state"] and not force_duplicate:
        raise ValueError(
            "Duplicate update prevented: no changes detected since the last recorded update "
            f"(fingerprint {git_info['fingerprint'][:12]}...). Pass --force-duplicate to override."
        )

    # Optional test command gate
    testing_text = plan.get("testing", "Not run.")
    test_cmd = config.get("test_command")
    if not dry_run and not skip_tests and (run_tests or (config["require_tests_before_release"] and test_cmd)):
        if test_cmd:
            test_res = _run_configured_tests(root, test_cmd)
            if not test_res["passed"]:
                raise ValueError(
                    f"Pre-release tests failed (exit code {test_res['returncode']}). "
                    "Release aborted.\n" + (test_res["stderr"] or test_res["stdout"])
                )
            testing_text = "Passed."

    bump_norm = plan["bump"]
    next_version = plan["next_version"]
    is_unreleased_note = bump_norm == "none"

    if not is_valid_semver(next_version):
        raise SemVerError(f"Invalid target SemVer '{next_version}'.")

    # Prepare synchronized version file writes
    prepared_writes: Dict[Path, str] = {}
    planned_edits_objs = []
    if not is_unreleased_note:
        planned_edits_objs, prepared_writes = plan_version_sync(
            root,
            detection["version_sources"],
            next_version,
            auto_update_dependent=config["auto_update_dependent_files"],
            extra_sync_files=config["sync_files"],
        )

    updates_rel = config["update_history"]
    changelog_rel = config["changelog"]
    updates_path = root / updates_rel
    changelog_path = root / changelog_rel

    if dry_run:
        return {
            "applied": False,
            "dry_run": True,
            "current_version": plan["current_version"],
            "next_version": next_version,
            "bump": bump_norm,
            "file_edits": [e.to_dict() for e in planned_edits_objs] if planned_edits_objs else plan.get("file_edits", []),
            "would_update_files": [
                *(str(p.relative_to(root).as_posix()) for p in prepared_writes),
                updates_rel,
                *([changelog_rel] if not is_unreleased_note else []),
            ],
        }

    # Step 1: Write synchronized version files atomically first so we can hash final code/manifest state
    if prepared_writes:
        apply_atomic_writes(prepared_writes)

    # Step 2: Recompute post-version-update workspace fingerprint (excluding history/changelog/state)
    excluded_meta = {updates_rel, changelog_rel}
    post_fp_info = compute_workspace_fingerprint(
        root,
        exclude_files=excluded_meta,
        extra_ignores=set(config["ignore_paths"]),
    )
    post_fp = post_fp_info["fingerprint"]

    # Step 3: Prepare PROJECT_UPDATES.md and CHANGELOG.md
    doc_writes: Dict[Path, str] = {}
    existing_updates = updates_path.read_text(encoding="utf-8") if updates_path.is_file() else None
    entry_md = format_project_update_entry(
        version=next_version,
        bump_type=bump_norm,
        category=plan["category"],
        summary=plan["summary"],
        changes=plan.get("changes", []),
        affected_areas=plan.get("affected_areas", []),
        compatibility=plan.get("compatibility", "Backward compatible."),
        testing=testing_text,
        migration_required=plan.get("migration_required", "No."),
        entry_date=plan.get("date"),
        fingerprint=post_fp,
        unreleased_note=is_unreleased_note,
    )
    doc_writes[updates_path] = prepare_project_updates_content(
        existing_updates,
        entry_md,
        version=next_version,
        unreleased_note=is_unreleased_note,
    )

    if not is_unreleased_note:
        existing_cl = changelog_path.read_text(encoding="utf-8") if changelog_path.is_file() else None
        cl_entry = format_changelog_entry(
            version=next_version,
            sections=plan.get("changelog_sections", {}),
            entry_date=plan.get("date"),
        )
        doc_writes[changelog_path] = prepare_changelog_content(
            existing_cl,
            cl_entry,
            version=next_version,
        )

    apply_atomic_writes(doc_writes)

    # Step 4: Save state file & validate resulting repository state
    head_sha = git_info["head_commit"]["sha"] if git_info.get("head_commit") else None
    save_recorded_state(root, version=next_version, fingerprint=post_fp, git_head=head_sha)

    post_validation = validate_project(root)
    return {
        "applied": True,
        "dry_run": False,
        "current_version": plan["current_version"],
        "next_version": next_version,
        "bump": bump_norm,
        "fingerprint": post_fp,
        "file_edits": [e.to_dict() for e in planned_edits_objs],
        "updated_docs": [str(p.relative_to(root).as_posix()) for p in doc_writes],
        "post_validation": post_validation,
    }


def initialize_project(
    root: Path,
    *,
    initial_version: Optional[str] = None,
    with_config: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    detection = detect_project(root)
    config = detection["config"]
    if detection["config_errors"]:
        raise ValueError("; ".join(detection["config_errors"]))

    ver = initial_version or config["initial_version"]
    if not is_valid_semver(ver):
        raise SemVerError(f"Invalid initial version '{ver}'.")

    if detection["version_sources"]:
        raise ValueError(
            "Version declaration(s) already exist: "
            + ", ".join(s["path"] for s in detection["version_sources"])
        )

    files_to_create: Dict[Path, str] = {}
    ver_file_path = root / config["version_file"]
    files_to_create[ver_file_path] = ver + "\n"

    updates_path = root / config["update_history"]
    if not updates_path.exists():
        files_to_create[updates_path] = UPDATES_HEADER

    changelog_path = root / config["changelog"]
    if not changelog_path.exists():
        files_to_create[changelog_path] = CHANGELOG_HEADER

    config_path = root / CONFIG_FILENAME
    if with_config and not config_path.exists():
        files_to_create[config_path] = json.dumps(DEFAULT_CONFIG, indent=2) + "\n"

    rel_paths = [str(p.relative_to(root).as_posix()) for p in files_to_create]
    if dry_run:
        return {
            "initialized": False,
            "dry_run": True,
            "initial_version": ver,
            "ecosystems": detection["ecosystems"],
            "would_create": rel_paths,
        }

    apply_atomic_writes(files_to_create)
    return {
        "initialized": True,
        "dry_run": False,
        "initial_version": ver,
        "ecosystems": detection["ecosystems"],
        "created": rel_paths,
    }


def _add_common_path_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("path", nargs="?", default=".", help="Project root path (default: .)")
    p.add_argument("--json", action="store_true", help="Output machine-readable JSON")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="version_manager.py",
        description="Project Version Manager CLI for deterministic version detection, planning, validation, and updates.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # detect
    p_detect = sub.add_parser("detect", help="Detect ecosystems, version files, and conflicts")
    _add_common_path_arg(p_detect)

    # status
    p_status = sub.add_parser("status", help="Show current version, history, git state, and readiness")
    _add_common_path_arg(p_status)

    # analyze
    p_analyze = sub.add_parser("analyze", help="Inspect Git commits, working-tree diffs, and fingerprint")
    _add_common_path_arg(p_analyze)

    # validate
    p_val = sub.add_parser("validate", help="Validate SemVer, config, and version consistency")
    _add_common_path_arg(p_val)
    p_val.add_argument("--strict", action="store_true", help="Fail if no version declaration exists")

    # init
    p_init = sub.add_parser("init", help="Safely initialize version tracking for an unversioned project")
    _add_common_path_arg(p_init)
    p_init.add_argument("--initial-version", help="Initial SemVer version (default from config or 0.1.0)")
    p_init.add_argument("--with-config", action="store_true", help="Also create .versioning.json if missing")
    p_init.add_argument("--dry-run", action="store_true", help="Preview files without writing")

    # plan
    p_plan = sub.add_parser("plan", help="Calculate next version and generate a release plan")
    _add_common_path_arg(p_plan)
    p_plan.add_argument("--bump", required=True, choices=["major", "minor", "patch", "prerelease", "none"])
    p_plan.add_argument("--pre-id", help="Prerelease identifier (e.g., alpha, beta, rc)")
    p_plan.add_argument("--build", help="Build metadata (e.g., build.105)")
    p_plan.add_argument("--target-version", help="Explicit target SemVer override")
    p_plan.add_argument("--allow-downgrade", action="store_true", help="Allow explicit target version below current")
    p_plan.add_argument("--category", help="Update category label (e.g., New Feature, Bug Fix)")
    p_plan.add_argument("--summary", help="Short summary of the update")
    p_plan.add_argument("--change", action="append", dest="changes", help="Change bullet (repeatable)")
    p_plan.add_argument("--area", action="append", dest="areas", help="Affected area (repeatable)")
    p_plan.add_argument("--added", action="append", default=[], help="Changelog Added item (repeatable)")
    p_plan.add_argument("--changed", action="append", default=[], help="Changelog Changed item (repeatable)")
    p_plan.add_argument("--fixed", action="append", default=[], help="Changelog Fixed item (repeatable)")
    p_plan.add_argument("--removed", action="append", default=[], help="Changelog Removed item (repeatable)")
    p_plan.add_argument("--deprecated", action="append", default=[], help="Changelog Deprecated item (repeatable)")
    p_plan.add_argument("--security", action="append", default=[], help="Changelog Security item (repeatable)")
    p_plan.add_argument("--compatibility", default="Backward compatible.")
    p_plan.add_argument("--testing", default="Not run.")
    p_plan.add_argument("--migration", default="No.")
    p_plan.add_argument("--out", help="Write release plan JSON to file")

    # apply
    p_apply = sub.add_parser("apply", help="Apply a release plan or explicit version bump")
    _add_common_path_arg(p_apply)
    p_apply.add_argument("--plan", help="Path to a release-plan.json file")
    p_apply.add_argument("--bump", choices=["major", "minor", "patch", "prerelease", "none"])
    p_apply.add_argument("--pre-id", help="Prerelease identifier when using --bump")
    p_apply.add_argument("--build", help="Build metadata")
    p_apply.add_argument("--target-version", help="Explicit target version")
    p_apply.add_argument("--category", help="Update category")
    p_apply.add_argument("--summary", help="Update summary")
    p_apply.add_argument("--change", action="append", dest="changes", help="Change bullet (repeatable)")
    p_apply.add_argument("--area", action="append", dest="areas", help="Affected area (repeatable)")
    p_apply.add_argument("--added", action="append", default=[])
    p_apply.add_argument("--changed", action="append", default=[])
    p_apply.add_argument("--fixed", action="append", default=[])
    p_apply.add_argument("--removed", action="append", default=[])
    p_apply.add_argument("--deprecated", action="append", default=[])
    p_apply.add_argument("--security", action="append", default=[])
    p_apply.add_argument("--compatibility", default="Backward compatible.")
    p_apply.add_argument("--testing", default="Not run.")
    p_apply.add_argument("--migration", default="No.")
    p_apply.add_argument("--dry-run", action="store_true", help="Preview edits without modifying files")
    p_apply.add_argument("--force-duplicate", action="store_true", help="Allow applying even if fingerprint matches last release")
    p_apply.add_argument("--run-tests", action="store_true", help="Run configured test_command before finalizing")
    p_apply.add_argument("--skip-tests", action="store_true", help="Skip configured test_command")

    # history
    p_hist = sub.add_parser("history", help="Display recorded project update history and changelog entries")
    _add_common_path_arg(p_hist)
    p_hist.add_argument("--limit", type=int, default=10, help="Maximum entries to show (default: 10)")

    # release-notes
    p_notes = sub.add_parser("release-notes", help="Generate release notes for a version")
    _add_common_path_arg(p_notes)
    p_notes.add_argument("--version", help="Target version (defaults to current version)")
    p_notes.add_argument("--out", help="Optional output markdown file")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    root = _resolve_root(args.path)

    try:
        if args.command == "detect":
            res = detect_project(root)
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                print(f"Ecosystems      : {', '.join(res['ecosystems'])}")
                print(f"Current Version : {res['current_version'] or 'Not detected'}")
                for s in res["version_sources"]:
                    print(f"  [{s['role']}] {s['path']}: {s['raw_version']}")
                if res["conflicts"]:
                    print("Conflicts:")
                    for c in res["conflicts"]:
                        print(f"  ! {c['message']}")
            return 0 if not res["config_errors"] else 1

        if args.command == "status":
            res = build_status(root)
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                print(f"Project Root       : {res['project_root']}")
                print(f"Mode               : {res['mode']}")
                print(f"Ecosystems         : {', '.join(res['ecosystems'])}")
                print(f"Current Version    : {res['current_version'] or 'Not detected'}")
                print(f"Previous Version   : {res['previous_version'] or 'None recorded'}")
                print(f"Git Branch / Tag   : {res['branch'] or 'N/A'} / {res['latest_semver_tag'] or 'None'}")
                print(f"Working Tree Clean : {res['working_tree_clean']}")
                print(f"Duplicate State    : {res['already_recorded_for_current_state']}")
                print(f"Ready for Release  : {res['ready_for_release']}")
                if res["validation_errors"]:
                    print("Validation Errors:")
                    for err in res["validation_errors"]:
                        print(f"  ! {err}")
            return 0

        if args.command == "analyze":
            det = detect_project(root)
            cfg = det["config"]
            res = inspect_changes(
                root,
                update_history_rel=cfg["update_history"],
                changelog_rel=cfg["changelog"],
                extra_ignores=cfg["ignore_paths"],
            )
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                print(f"Git Available       : {res['git_available']}")
                print(f"Branch              : {res['branch'] or 'N/A'}")
                print(f"Latest SemVer Tag   : {res['latest_semver_tag'] or 'None'}")
                print(f"Commits Since Tag   : {res['total_commits_since_tag']}")
                print(f"Workspace Clean     : {res['working_tree']['clean']}")
                print(f"Fingerprint         : {res['fingerprint'][:16]}...")
                print(f"Already Recorded    : {res['already_recorded_for_current_state']}")
                if res["commits_since_tag"]:
                    print("\nRecent Commits:")
                    for c in res["commits_since_tag"][:10]:
                        print(f"  - {c['sha'][:7]} {c['subject']}")
                if res["diff_stat"]:
                    print(f"\n{res['diff_stat']}")
                if res["limitations"]:
                    print("\nNotes:")
                    for lim in res["limitations"]:
                        print(f"  * {lim}")
            return 0

        if args.command == "validate":
            res = validate_project(root, strict=args.strict)
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                print(f"Validation: {'PASS' if res['valid'] else 'FAIL'}")
                for chk in res["checks"]:
                    print(f"  [{'PASS' if chk['passed'] else 'FAIL'}] {chk['name']}: {'; '.join(chk['details'])}")
                for err in res["errors"]:
                    print(f"  ! {err}")
            return 0 if res["valid"] else 1

        if args.command == "init":
            res = initialize_project(
                root,
                initial_version=args.initial_version,
                with_config=args.with_config,
                dry_run=args.dry_run,
            )
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                action = "Would create" if args.dry_run else "Created"
                print(f"{action} initial version {res['initial_version']}:")
                for p in res.get("created") or res.get("would_create") or []:
                    print(f"  - {p}")
            return 0

        if args.command == "plan":
            cl_sections = {
                "Added": args.added,
                "Changed": args.changed,
                "Fixed": args.fixed,
                "Removed": args.removed,
                "Deprecated": args.deprecated,
                "Security": args.security,
            }
            plan = build_plan(
                root,
                bump=args.bump,
                pre_id=args.pre_id,
                build=args.build,
                target_version=args.target_version,
                allow_downgrade=args.allow_downgrade,
                category=args.category,
                summary=args.summary,
                changes_list=args.changes,
                affected_areas=args.areas,
                changelog_sections=cl_sections,
                compatibility=args.compatibility,
                testing=args.testing,
                migration_required=args.migration,
            )
            if args.out:
                out_path = Path(args.out)
                if not out_path.is_absolute():
                    out_path = root / out_path
                out_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
            if args.json or not args.out:
                print(json.dumps(plan, indent=2))
            else:
                print(f"Planned {plan['current_version']} -> {plan['next_version']} ({plan['bump']})")
                print(f"Plan written to {args.out}")
            return 0

        if args.command == "apply":
            explicit = bool(args.plan or args.bump or args.target_version)
            if args.plan:
                plan_path = Path(args.plan)
                if not plan_path.is_absolute():
                    plan_path = root / plan_path
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
            elif args.bump or args.target_version:
                cl_sections = {
                    "Added": args.added,
                    "Changed": args.changed,
                    "Fixed": args.fixed,
                    "Removed": args.removed,
                    "Deprecated": args.deprecated,
                    "Security": args.security,
                }
                plan = build_plan(
                    root,
                    bump=args.bump or "patch",
                    pre_id=args.pre_id,
                    build=args.build,
                    target_version=args.target_version,
                    category=args.category,
                    summary=args.summary,
                    changes_list=args.changes,
                    affected_areas=args.areas,
                    changelog_sections=cl_sections,
                    compatibility=args.compatibility,
                    testing=args.testing,
                    migration_required=args.migration,
                )
            else:
                raise ValueError("Provide either --plan <file> or --bump <category>.")

            res = apply_plan(
                root,
                plan,
                dry_run=args.dry_run,
                force_duplicate=args.force_duplicate,
                run_tests=args.run_tests,
                skip_tests=args.skip_tests,
                explicit_user_request=explicit,
            )
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                if res["dry_run"]:
                    print(f"[DRY RUN] Would update {res['current_version']} -> {res['next_version']}")
                    for f in res["would_update_files"]:
                        print(f"  - {f}")
                else:
                    print(f"Updated {res['current_version']} -> {res['next_version']}")
                    for e in res["file_edits"]:
                        if e["action"] == "update":
                            print(f"  [updated] {e['path']}: {e['old_version']} -> {e['new_version']}")
                        else:
                            print(f"  [skipped] {e['path']}: {e['reason']}")
                    for d in res["updated_docs"]:
                        print(f"  [updated] {d}")
            return 0

        if args.command == "history":
            cfg, _, _, _ = load_config(root)
            updates = parse_project_updates(root / cfg["update_history"])[: args.limit]
            cl_sections = parse_changelog_versions(root / cfg["changelog"])[: args.limit]
            payload = {"project_updates": updates, "changelog": cl_sections}
            if args.json:
                print(json.dumps(payload, indent=2))
            else:
                print(f"Project Updates ({len(updates)} shown):")
                for u in updates:
                    print(f"  - {u['heading']} [{u.get('update_type') or 'N/A'}]: {u.get('summary') or ''}")
                print(f"\nChangelog Sections ({len(cl_sections)} shown):")
                for s in cl_sections:
                    print(f"  - [{s['version']}] {s.get('date') or ''}")
            return 0

        if args.command == "release-notes":
            det = detect_project(root)
            cfg = det["config"]
            target_ver = args.version or det["current_version"]
            if not target_ver:
                raise ValueError("No version specified and none detected in project.")
            notes = generate_release_notes(
                root,
                target_ver,
                changelog_rel=cfg["changelog"],
                updates_rel=cfg["update_history"],
            )
            if args.out and notes["found"]:
                out_path = Path(args.out)
                if not out_path.is_absolute():
                    out_path = root / out_path
                out_path.write_text(notes["markdown"], encoding="utf-8")
            if args.json:
                print(json.dumps(notes, indent=2))
            elif notes["found"]:
                print(notes["markdown"], end="")
            else:
                print(notes["message"], file=sys.stderr)
                return 1
            return 0

    except (ValueError, SemVerError, json.JSONDecodeError, OSError) as exc:
        if getattr(args, "json", False):
            print(json.dumps({"error": str(exc)}, indent=2))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
