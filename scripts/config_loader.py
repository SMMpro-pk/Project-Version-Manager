#!/usr/bin/env python3
"""Load and validate optional .versioning.json project configuration."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from semver_utils import VALID_ZERO_POLICIES, is_valid_semver

CONFIG_FILENAME = ".versioning.json"
VALID_MODES = ("auto", "suggest", "manual")
VALID_STRATEGIES = ("semver",)

DEFAULT_CONFIG: Dict[str, Any] = {
    "schema_version": 1,
    "mode": "auto",
    "strategy": "semver",
    "initial_version": "0.1.0",
    "zero_major_policy": "breaking_to_minor",
    "update_history": "PROJECT_UPDATES.md",
    "changelog": "CHANGELOG.md",
    "version_file": "VERSION",
    "include_documentation_only_changes": False,
    "auto_update_dependent_files": True,
    "require_tests_before_release": True,
    "create_git_tags": False,
    "create_git_commits": False,
    "test_command": None,
    "ignore_paths": [],
    "authoritative_file": None,
    "sync_files": [],
}

BOOLEAN_KEYS = (
    "include_documentation_only_changes",
    "auto_update_dependent_files",
    "require_tests_before_release",
    "create_git_tags",
    "create_git_commits",
)

STRING_KEYS = (
    "update_history",
    "changelog",
    "version_file",
)


def load_config(project_root: Path) -> Tuple[Dict[str, Any], List[str], List[str], Optional[Path]]:
    """Load .versioning.json if present, merge with defaults, and validate fields.

    Returns:
        (config_dict, warnings, errors, config_path_or_none)
    """
    config = copy.deepcopy(DEFAULT_CONFIG)
    warnings: List[str] = []
    errors: List[str] = []

    config_path = project_root / CONFIG_FILENAME
    if not config_path.exists():
        return config, warnings, errors, None

    try:
        raw_text = config_path.read_text(encoding="utf-8")
        user_data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        errors.append(f"Invalid JSON in {CONFIG_FILENAME}: {exc}")
        return config, warnings, errors, config_path
    except OSError as exc:
        errors.append(f"Could not read {CONFIG_FILENAME}: {exc}")
        return config, warnings, errors, config_path

    if not isinstance(user_data, dict):
        errors.append(f"{CONFIG_FILENAME} must contain a JSON object at the top level.")
        return config, warnings, errors, config_path

    unknown_keys = sorted(set(user_data.keys()) - set(DEFAULT_CONFIG.keys()))
    if unknown_keys:
        warnings.append(
            f"Unknown configuration option(s) in {CONFIG_FILENAME}: {', '.join(unknown_keys)}"
        )

    for key, value in user_data.items():
        if key in DEFAULT_CONFIG:
            config[key] = value

    # Validate individual fields
    if config["schema_version"] != 1:
        errors.append(
            f"Unsupported schema_version '{config['schema_version']}' in {CONFIG_FILENAME}; expected 1."
        )

    if config["mode"] not in VALID_MODES:
        errors.append(
            f"Invalid mode '{config['mode']}'. Allowed: {', '.join(VALID_MODES)}."
        )

    if config["strategy"] not in VALID_STRATEGIES:
        errors.append(
            f"Invalid strategy '{config['strategy']}'. Allowed: {', '.join(VALID_STRATEGIES)}."
        )

    if not isinstance(config["initial_version"], str) or not is_valid_semver(config["initial_version"]):
        errors.append(
            f"Invalid initial_version '{config['initial_version']}'. Must be valid SemVer (e.g., 0.1.0)."
        )

    if config["zero_major_policy"] not in VALID_ZERO_POLICIES:
        errors.append(
            f"Invalid zero_major_policy '{config['zero_major_policy']}'. "
            f"Allowed: {', '.join(VALID_ZERO_POLICIES)}."
        )

    for key in BOOLEAN_KEYS:
        if not isinstance(config[key], bool):
            errors.append(f"Configuration key '{key}' must be a boolean.")

    for key in STRING_KEYS:
        if not isinstance(config[key], str) or not config[key].strip():
            errors.append(f"Configuration key '{key}' must be a non-empty string.")

    for list_key in ("ignore_paths", "sync_files"):
        if not isinstance(config[list_key], list) or not all(
            isinstance(item, str) for item in config[list_key]
        ):
            errors.append(f"Configuration key '{list_key}' must be an array of strings.")

    for opt_str_key in ("test_command", "authoritative_file"):
        val = config[opt_str_key]
        if val is not None and (not isinstance(val, str) or not val.strip()):
            errors.append(f"Configuration key '{opt_str_key}' must be null or a non-empty string.")

    if config["create_git_tags"] or config["create_git_commits"]:
        warnings.append(
            "Automatic Git commit/tag creation is enabled in .versioning.json; "
            "ensure repository state is clean before applying releases."
        )

    return config, warnings, errors, config_path

