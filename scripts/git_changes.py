#!/usr/bin/env python3
"""Git and filesystem change inspection with SHA-256 fingerprinting."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from semver_utils import parse_semver, is_valid_semver

DEFAULT_IGNORE_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    "dist",
    "build",
    "target",
    "vendor",
    ".pytest_cache",
    ".mypy_cache",
    ".tox",
    ".next",
    ".nuxt",
    ".idea",
    ".vscode",
}

STATE_FILENAME = ".versioning-state.json"
FINGERPRINT_COMMENT_RE = re.compile(r"<!--\s*pvm-fingerprint:\s*([a-f0-9]{64})\s*-->")


def _run_git(root: Path, args: List[str], timeout: int = 10) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def is_git_repository(root: Path) -> Tuple[bool, Optional[str]]:
    """Return (is_git_repo, diagnostic_message)."""
    try:
        proc = _run_git(root, ["rev-parse", "--is-inside-work-tree"])
        if proc.returncode == 0 and proc.stdout.strip().lower() == "true":
            return True, None
        return False, proc.stderr.strip() or "Directory is not a Git repository."
    except FileNotFoundError:
        return False, "Git executable is not installed or not on PATH."
    except Exception as exc:
        return False, f"Git check failed: {exc}"


def _should_ignore(rel_posix: str, extra_ignores: Set[str]) -> bool:
    parts = rel_posix.split("/")
    if any(part in DEFAULT_IGNORE_DIRS for part in parts):
        return True
    for ign in extra_ignores:
        ign_clean = ign.strip().strip("/")
        if not ign_clean:
            continue
        if rel_posix == ign_clean or rel_posix.startswith(ign_clean + "/") or ign_clean in parts:
            return True
    return False


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def compute_workspace_fingerprint(
    root: Path,
    *,
    exclude_files: Optional[Set[str]] = None,
    extra_ignores: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """Compute a deterministic SHA-256 fingerprint over non-ignored workspace files."""
    excluded = {p.replace("\\", "/") for p in (exclude_files or set())}
    excluded.update({STATE_FILENAME, "release-plan.json"})
    ignores = set(extra_ignores or set())

    file_records: List[Tuple[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        # Prune ignored directories in-place for speed
        dirnames[:] = sorted(
            d
            for d in dirnames
            if d not in DEFAULT_IGNORE_DIRS
            and not _should_ignore(d if rel_dir == "." else f"{rel_dir}/{d}", ignores)
        )
        for fname in sorted(filenames):
            rel_path = fname if rel_dir == "." else f"{rel_dir}/{fname}"
            if rel_path in excluded or _should_ignore(rel_path, ignores):
                continue
            full_path = Path(dirpath) / fname
            if full_path.is_symlink():
                continue
            try:
                digest = _hash_file(full_path)
                file_records.append((rel_path, digest))
            except OSError:
                continue

    file_records.sort(key=lambda item: item[0])
    master = hashlib.sha256()
    for rel_path, digest in file_records:
        master.update(rel_path.encode("utf-8"))
        master.update(b":")
        master.update(digest.encode("ascii"))
        master.update(b"\n")

    return {
        "fingerprint": master.hexdigest(),
        "file_count": len(file_records),
    }


def load_last_recorded_state(root: Path, update_history_rel: str) -> Dict[str, Any]:
    """Read last applied fingerprint from .versioning-state.json or PROJECT_UPDATES.md."""
    state_path = root / STATE_FILENAME
    state_data: Dict[str, Any] = {}
    if state_path.is_file():
        try:
            loaded = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                state_data = loaded
        except Exception:
            pass

    if not state_data.get("last_fingerprint"):
        history_path = root / update_history_rel
        if history_path.is_file():
            try:
                text = history_path.read_text(encoding="utf-8", errors="replace")
                match = FINGERPRINT_COMMENT_RE.search(text)
                if match:
                    state_data["last_fingerprint"] = match.group(1)
                    state_data.setdefault("source", update_history_rel)
            except OSError:
                pass
    else:
        state_data.setdefault("source", STATE_FILENAME)

    return state_data


def save_recorded_state(
    root: Path,
    *,
    version: str,
    fingerprint: str,
    git_head: Optional[str],
) -> None:
    """Persist last applied release fingerprint safely."""
    state_path = root / STATE_FILENAME
    payload = {
        "last_version": version,
        "last_fingerprint": fingerprint,
        "last_git_head": git_head,
    }
    tmp_path = root / f"{STATE_FILENAME}.tmp"
    tmp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp_path, state_path)


def _get_semver_tags(root: Path) -> List[Tuple[Any, str]]:
    proc = _run_git(root, ["tag", "--list"])
    if proc.returncode != 0:
        return []
    tags = []
    for line in proc.stdout.splitlines():
        tag = line.strip()
        if tag and is_valid_semver(tag, strip_v=True):
            tags.append((parse_semver(tag, strip_v=True), tag))
    tags.sort(key=lambda item: item[0], reverse=True)
    return tags


def _parse_porcelain_z(root: Path, extra_ignores: Set[str]) -> Dict[str, List[str]]:
    proc = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "-uall"],
        cwd=str(root),
        capture_output=True,
        timeout=10,
        check=False,
    )
    buckets: Dict[str, List[str]] = {
        "staged": [],
        "unstaged": [],
        "untracked": [],
        "added": [],
        "modified": [],
        "deleted": [],
        "renamed": [],
    }
    if proc.returncode != 0:
        return buckets

    raw = proc.stdout.decode("utf-8", errors="replace")
    entries = raw.split("\0")
    idx = 0
    while idx < len(entries):
        entry = entries[idx]
        idx += 1
        if not entry or len(entry) < 4:
            continue
        x, y = entry[0], entry[1]
        path = entry[3:].replace("\\", "/")
        if x == "R" or y == "R":
            # Next NUL entry is original path in porcelain -z
            if idx < len(entries):
                idx += 1
        if _should_ignore(path, extra_ignores):
            continue

        if x == "?" and y == "?":
            buckets["untracked"].append(path)
            buckets["added"].append(path)
            continue

        if x != " ":
            buckets["staged"].append(path)
        if y != " ":
            buckets["unstaged"].append(path)

        if "D" in (x, y):
            buckets["deleted"].append(path)
        elif "R" in (x, y):
            buckets["renamed"].append(path)
        elif "A" in (x, y):
            buckets["added"].append(path)
        else:
            buckets["modified"].append(path)

    return buckets


def inspect_changes(
    project_root: Path,
    *,
    update_history_rel: str = "PROJECT_UPDATES.md",
    changelog_rel: str = "CHANGELOG.md",
    extra_ignores: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Inspect Git commits, working-tree changes, and workspace fingerprint."""
    root = project_root.resolve()
    ignores = set(extra_ignores or [])
    excluded_meta = {update_history_rel, changelog_rel}

    git_ok, git_diag = is_git_repository(root)
    fp_info = compute_workspace_fingerprint(
        root, exclude_files=excluded_meta, extra_ignores=ignores
    )
    last_state = load_last_recorded_state(root, update_history_rel)
    last_fp = last_state.get("last_fingerprint")
    duplicate_state = bool(last_fp and last_fp == fp_info["fingerprint"])

    result: Dict[str, Any] = {
        "git_available": git_ok,
        "git_diagnostic": git_diag,
        "branch": None,
        "head_commit": None,
        "latest_semver_tag": None,
        "previous_semver_tag": None,
        "commits_since_tag": [],
        "total_commits_since_tag": 0,
        "working_tree": {
            "clean": True,
            "staged": [],
            "unstaged": [],
            "untracked": [],
            "added": [],
            "modified": [],
            "deleted": [],
            "renamed": [],
        },
        "changed_files_since_tag": [],
        "diff_stat": "",
        "fingerprint": fp_info["fingerprint"],
        "indexed_file_count": fp_info["file_count"],
        "last_recorded_fingerprint": last_fp,
        "last_recorded_version": last_state.get("last_version"),
        "already_recorded_for_current_state": duplicate_state,
        "limitations": [],
    }

    if not git_ok:
        result["limitations"].append(
            f"Git history unavailable ({git_diag}). Using filesystem fingerprint fallback."
        )
        return result

    # Branch & HEAD
    branch_proc = _run_git(root, ["rev-parse", "--abbrev-ref", "HEAD"])
    if branch_proc.returncode == 0:
        result["branch"] = branch_proc.stdout.strip()

    head_proc = _run_git(root, ["log", "-1", "--format=%H%n%s%n%cI"])
    if head_proc.returncode == 0 and head_proc.stdout.strip():
        lines = head_proc.stdout.splitlines()
        result["head_commit"] = {
            "sha": lines[0] if len(lines) > 0 else "",
            "subject": lines[1] if len(lines) > 1 else "",
            "date": lines[2] if len(lines) > 2 else "",
        }

    # Tags & commits since tag
    tags = _get_semver_tags(root)
    if tags:
        result["latest_semver_tag"] = tags[0][1]
        if len(tags) > 1:
            result["previous_semver_tag"] = tags[1][1]

    if result["head_commit"]:
        rev_range = f"{tags[0][1]}..HEAD" if tags else "HEAD"
        log_proc = _run_git(root, ["log", rev_range, "--max-count=100", "--format=%H\t%s\t%cI"])
        if log_proc.returncode == 0:
            commits = []
            for line in log_proc.stdout.splitlines():
                if not line.strip():
                    continue
                parts = line.split("\t", 2)
                commits.append(
                    {
                        "sha": parts[0],
                        "subject": parts[1] if len(parts) > 1 else "",
                        "date": parts[2] if len(parts) > 2 else "",
                    }
                )
            result["commits_since_tag"] = commits
            result["total_commits_since_tag"] = len(commits)

        if tags:
            files_proc = _run_git(root, ["diff", "--name-only", f"{tags[0][1]}..HEAD"])
            if files_proc.returncode == 0:
                changed = [
                    p.strip().replace("\\", "/")
                    for p in files_proc.stdout.splitlines()
                    if p.strip() and not _should_ignore(p.strip().replace("\\", "/"), ignores)
                ]
                result["changed_files_since_tag"] = changed

    # Working tree changes
    wt = _parse_porcelain_z(root, ignores)
    is_clean = not any(wt[k] for k in ("staged", "unstaged", "untracked"))
    result["working_tree"] = {"clean": is_clean, **wt}

    stat_parts = []
    for diff_args, label in (
        (["diff", "--cached", "--stat"], "Staged"),
        (["diff", "--stat"], "Unstaged"),
    ):
        dp = _run_git(root, diff_args)
        if dp.returncode == 0 and dp.stdout.strip():
            stat_parts.append(f"[{label}]\n{dp.stdout.strip()}")
    if tags and result["head_commit"]:
        tp = _run_git(root, ["diff", "--stat", f"{tags[0][1]}..HEAD"])
        if tp.returncode == 0 and tp.stdout.strip():
            stat_parts.append(f"[Since {tags[0][1]}]\n{tp.stdout.strip()}")
    result["diff_stat"] = "\n\n".join(stat_parts)

    return result

