#!/usr/bin/env python3
"""Validate project configuration, version strings, synchronization, and release readiness."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

if __package__:
    from .detect_version import detect_project
    from .history_manager import parse_changelog_versions, parse_project_updates
    from .semver_utils import is_valid_semver, parse_semver
else:
    from detect_version import detect_project
    from history_manager import parse_changelog_versions, parse_project_updates
    from semver_utils import is_valid_semver, parse_semver


def validate_project(
    project_root: Path,
    *,
    strict: bool = False,
    precomputed_detection: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run comprehensive validation checks on a project."""
    root = project_root.resolve()
    detection = precomputed_detection or detect_project(root)
    config = detection["config"]

    errors: List[str] = list(detection["config_errors"])
    warnings: List[str] = list(detection["config_warnings"])
    checks: List[Dict[str, Any]] = []

    # 1. Config check
    checks.append(
        {
            "name": "configuration",
            "passed": len(detection["config_errors"]) == 0,
            "details": detection["config_errors"] or ["Configuration is valid."],
        }
    )

    # 2. Version presence & SemVer validity
    current_ver = detection["current_version"]
    sources = detection["version_sources"]
    if not sources:
        msg = "No version declaration detected."
        if strict:
            errors.append(msg)
        else:
            warnings.append(msg)
        checks.append({"name": "version_detected", "passed": not strict, "details": [msg]})
    elif current_ver is None:
        msg = "Authoritative version could not be determined unambiguously."
        errors.append(msg)
        checks.append({"name": "version_detected", "passed": False, "details": [msg]})
    else:
        invalid_sources = [s for s in sources if not s["semver_valid"]]
        if invalid_sources:
            for s in invalid_sources:
                errors.append(
                    f"Non-SemVer version '{s['raw_version']}' in {s['path']}."
                )
            checks.append(
                {
                    "name": "semver_format",
                    "passed": False,
                    "details": [f"{s['path']}: {s['raw_version']}" for s in invalid_sources],
                }
            )
        else:
            checks.append(
                {
                    "name": "semver_format",
                    "passed": True,
                    "details": [f"All detected versions conform to SemVer 2.0.0 ({current_ver})."],
                }
            )

    # 3. Cross-file consistency
    conflicts = detection["conflicts"]
    if conflicts:
        for c in conflicts:
            errors.append(c["message"])
        checks.append(
            {
                "name": "version_consistency",
                "passed": False,
                "details": [c["message"] for c in conflicts],
            }
        )
    else:
        checks.append(
            {
                "name": "version_consistency",
                "passed": True,
                "details": ["No version conflicts across detected files."],
            }
        )

    # 4. History & Changelog sanity
    updates_path = root / config["update_history"]
    changelog_path = root / config["changelog"]
    history_issues: List[str] = []

    if updates_path.is_file():
        entries = parse_project_updates(updates_path)
        seen_versions = set()
        prev_sv = None
        for entry in entries:
            v = entry.get("version")
            if v:
                if not is_valid_semver(v):
                    history_issues.append(f"Invalid SemVer '{v}' in {config['update_history']}.")
                else:
                    sv = parse_semver(v)
                    if prev_sv is not None and sv > prev_sv:
                        warnings.append(
                            f"{config['update_history']} entry '{v}' is newer than preceding entry '{prev_sv}' (expected newest first)."
                        )
                    prev_sv = sv
                if v in seen_versions:
                    history_issues.append(
                        f"Duplicate version '{v}' found in {config['update_history']}."
                    )
                seen_versions.add(v)

    if changelog_path.is_file():
        cl_sections = parse_changelog_versions(changelog_path)
        seen_cl = set()
        released_versions = []
        prev_cl_sv = None
        for sec in cl_sections:
            v = sec["version"]
            if v.lower() == "unreleased":
                continue
            released_versions.append(v)
            if not is_valid_semver(v):
                history_issues.append(f"Invalid SemVer '[{v}]' in {config['changelog']}.")
            else:
                sv = parse_semver(v)
                if prev_cl_sv is not None and sv > prev_cl_sv:
                    warnings.append(
                        f"{config['changelog']} section '[{v}]' is newer than preceding section '[{prev_cl_sv}]' (expected newest first)."
                    )
                prev_cl_sv = sv
            if v in seen_cl:
                history_issues.append(f"Duplicate section '[{v}]' in {config['changelog']}.")
            seen_cl.add(v)
        if current_ver and released_versions and current_ver not in seen_cl:
            warnings.append(
                f"{config['changelog']} does not yet contain a section for current version [{current_ver}]."
            )

    if history_issues:
        errors.extend(history_issues)
        checks.append({"name": "history_integrity", "passed": False, "details": history_issues})
    else:
        checks.append(
            {
                "name": "history_integrity",
                "passed": True,
                "details": ["Update history and changelog structure are valid."],
            }
        )

    valid = len(errors) == 0
    return {
        "valid": valid,
        "project_root": str(root),
        "current_version": current_ver,
        "ecosystems": detection["ecosystems"],
        "checks": checks,
        "errors": errors,
        "warnings": warnings,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate SemVer strings, configuration, and cross-file version consistency."
    )
    parser.add_argument("path", nargs="?", default=".", help="Project root path (default: .)")
    parser.add_argument("--version", dest="check_version", help="Validate a standalone SemVer string")
    parser.add_argument("--strict", action="store_true", help="Fail if no version declaration exists")
    parser.add_argument("--json", action="store_true", help="Output result as JSON")
    args = parser.parse_args(argv)

    if args.check_version is not None:
        ok = is_valid_semver(args.check_version)
        if args.json:
            print(json.dumps({"version": args.check_version, "valid": ok}, indent=2))
        else:
            print(
                f"VALID: '{args.check_version}' conforms to SemVer 2.0.0"
                if ok
                else f"INVALID: '{args.check_version}' is not valid SemVer 2.0.0"
            )
        return 0 if ok else 1

    result = validate_project(Path(args.path), strict=args.strict)
    if args.json:
        print(json.dumps(result, indent=2))
        return 0 if result["valid"] else 1

    print(f"Validation Status : {'PASS' if result['valid'] else 'FAIL'}")
    print(f"Current Version   : {result['current_version'] or 'None'}")
    for chk in result["checks"]:
        badge = "PASS" if chk["passed"] else "FAIL"
        print(f"  [{badge}] {chk['name']}: {'; '.join(chk['details'])}")
    if result["warnings"]:
        print("\nWarnings:")
        for w in result["warnings"]:
            print(f"  * {w}")
    if result["errors"]:
        print("\nErrors:")
        for e in result["errors"]:
            print(f"  ! {e}")
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
