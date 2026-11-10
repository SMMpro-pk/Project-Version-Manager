#!/usr/bin/env python3
"""Manage PROJECT_UPDATES.md, CHANGELOG.md, and release notes generation."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

UPDATES_HEADER = (
    "# Project Update History\n\n"
    "This file maintains a chronological record of meaningful project updates "
    "for developers and AI agents.\n\n"
    "## Project Update History\n"
)

CHANGELOG_HEADER = (
    "# Changelog\n\n"
    "All notable changes to this project will be documented in this file.\n\n"
    "The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),\n"
    "and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).\n\n"
    "## [Unreleased]\n"
)

ALLOWED_CHANGELOG_SECTIONS = (
    "Added",
    "Changed",
    "Deprecated",
    "Removed",
    "Fixed",
    "Security",
)

VERSION_HEADING_RE = re.compile(
    r"^###\s+Version\s+([^\s—-]+)\s+[—-]\s+(\d{4}-\d{2}-\d{2})\s*$",
    re.MULTILINE,
)
CHANGELOG_HEADING_RE = re.compile(
    r"^##\s+\[([^\]]+)\](?:\s*-\s*(\d{4}-\d{2}-\d{2}))?\s*$",
    re.MULTILINE,
)


def parse_project_updates(history_path: Path) -> List[Dict[str, Any]]:
    """Parse structured entries from PROJECT_UPDATES.md."""
    if not history_path.is_file():
        return []
    text = history_path.read_text(encoding="utf-8", errors="replace")
    matches = list(re.finditer(r"^###\s+(.+)$", text, re.MULTILINE))
    entries: List[Dict[str, Any]] = []

    for idx, m in enumerate(matches):
        heading = m.group(1).strip()
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        block = text[start:end].strip()

        ver_match = re.match(r"^Version\s+([^\s—-]+)\s+[—-]\s+(\d{4}-\d{2}-\d{2})$", heading)
        version = ver_match.group(1) if ver_match else None
        entry_date = ver_match.group(2) if ver_match else None
        if not entry_date:
            date_m = re.search(r"(\d{4}-\d{2}-\d{2})$", heading)
            entry_date = date_m.group(1) if date_m else None

        def _field(label: str) -> Optional[str]:
            fm = re.search(rf"^\*\*{re.escape(label)}:\*\*\s*(.+)$", block, re.MULTILINE)
            return fm.group(1).strip() if fm else None

        summary = ""
        sum_m = re.search(
            r"^\*\*Summary:\*\*\s*\n(.*?)(?=\n\*\*[A-Z]|\n---|\Z)",
            block,
            re.MULTILINE | re.DOTALL,
        )
        if sum_m:
            summary = sum_m.group(1).strip()

        def _bullets(label: str) -> List[str]:
            bm = re.search(
                rf"^\*\*{re.escape(label)}:\*\*\s*\n(.*?)(?=\n\*\*[A-Z]|\n---|\Z)",
                block,
                re.MULTILINE | re.DOTALL,
            )
            if not bm:
                return []
            items = []
            for line in bm.group(1).splitlines():
                stripped = line.strip()
                if stripped.startswith("- "):
                    items.append(stripped[2:].strip())
            return items

        entries.append(
            {
                "heading": heading,
                "version": version,
                "date": entry_date,
                "update_type": _field("Update Type"),
                "category": _field("Category"),
                "summary": summary,
                "changes": _bullets("Changes"),
                "affected_areas": _bullets("Affected Areas"),
                "compatibility": _field("Compatibility"),
                "testing": _field("Testing"),
                "migration_required": _field("Migration Required"),
            }
        )
    return entries


def has_project_update_version(text: str, version: str) -> bool:
    for m in VERSION_HEADING_RE.finditer(text):
        if m.group(1).strip() == version:
            return True
    return False


def format_project_update_entry(
    *,
    version: str,
    bump_type: str,
    category: str,
    summary: str,
    changes: List[str],
    affected_areas: List[str],
    compatibility: str = "Backward compatible.",
    testing: str = "Not run.",
    migration_required: str = "No.",
    entry_date: Optional[str] = None,
    fingerprint: Optional[str] = None,
    unreleased_note: bool = False,
) -> str:
    """Format a single structured PROJECT_UPDATES.md entry."""
    iso_date = entry_date or date.today().isoformat()
    if unreleased_note:
        heading = f"### Unreleased Update (v{version}) — {iso_date}"
    else:
        heading = f"### Version {version} — {iso_date}"

    lines = [
        heading,
        "",
        f"**Update Type:** {bump_type.upper()}",
        "",
        f"**Category:** {category}",
        "",
        "**Summary:**",
        summary.strip(),
        "",
        "**Changes:**",
    ]
    for item in (changes or ["No detailed change items recorded."]):
        lines.append(f"- {item.strip()}")

    if affected_areas:
        lines.extend(["", "**Affected Areas:**"])
        for area in affected_areas:
            lines.append(f"- {area.strip()}")

    lines.extend(
        [
            "",
            f"**Compatibility:** {compatibility}",
            "",
            f"**Testing:** {testing}",
            "",
            f"**Migration Required:** {migration_required}",
        ]
    )
    if fingerprint:
        lines.extend(["", f"<!-- pvm-fingerprint: {fingerprint} -->"])
    lines.extend(["", "---", ""])
    return "\n".join(lines)


def prepare_project_updates_content(
    existing_text: Optional[str],
    entry_markdown: str,
    *,
    version: str,
    unreleased_note: bool = False,
) -> str:
    """Prepend a new entry to PROJECT_UPDATES.md while preserving history."""
    if existing_text is None or not existing_text.strip():
        return UPDATES_HEADER + "\n" + entry_markdown

    if not unreleased_note and has_project_update_version(existing_text, version):
        raise ValueError(
            f"PROJECT_UPDATES.md already contains an entry for Version {version}."
        )

    anchor_match = re.search(r"^##\s+Project Update History\s*$", existing_text, re.MULTILINE)
    if anchor_match:
        insert_pos = anchor_match.end()
        before = existing_text[:insert_pos].rstrip("\n")
        after = existing_text[insert_pos:].lstrip("\n")
        return before + "\n\n" + entry_markdown + ("\n" + after if after else "")

    # If file uses a custom title without the standard subheading, insert before first '### '
    first_entry = re.search(r"^###\s+", existing_text, re.MULTILINE)
    if first_entry:
        pos = first_entry.start()
        return existing_text[:pos] + entry_markdown + "\n" + existing_text[pos:]

    return existing_text.rstrip("\n") + "\n\n## Project Update History\n\n" + entry_markdown


def has_changelog_version(text: str, version: str) -> bool:
    for m in CHANGELOG_HEADING_RE.finditer(text):
        if m.group(1).strip() == version:
            return True
    return False


def format_changelog_entry(
    *,
    version: str,
    sections: Dict[str, List[str]],
    entry_date: Optional[str] = None,
) -> str:
    """Format a Keep-a-Changelog release section."""
    iso_date = entry_date or date.today().isoformat()
    lines = [f"## [{version}] - {iso_date}", ""]
    wrote_any = False
    for sec in ALLOWED_CHANGELOG_SECTIONS:
        items = [i.strip() for i in sections.get(sec, []) if i and i.strip()]
        if items:
            wrote_any = True
            lines.append(f"### {sec}")
            for item in items:
                lines.append(f"- {item}")
            lines.append("")
    if not wrote_any:
        lines.extend(["### Changed", "- Maintenance update.", ""])
    return "\n".join(lines)


def prepare_changelog_content(
    existing_text: Optional[str],
    changelog_entry: str,
    *,
    version: str,
) -> str:
    """Insert a released version section into CHANGELOG.md without duplicating or losing history."""
    if existing_text is None or not existing_text.strip():
        return CHANGELOG_HEADER + "\n" + changelog_entry

    if has_changelog_version(existing_text, version):
        raise ValueError(f"CHANGELOG.md already contains section [{version}].")

    unreleased_m = re.search(r"^##\s+\[Unreleased\]\s*$", existing_text, re.MULTILINE)
    if unreleased_m:
        # Find the next ## [heading] after [Unreleased]
        after_unreleased = existing_text[unreleased_m.end() :]
        next_heading = re.search(r"^##\s+\[", after_unreleased, re.MULTILINE)
        if next_heading:
            insert_at = unreleased_m.end() + next_heading.start()
            before = existing_text[:insert_at].rstrip("\n")
            after = existing_text[insert_at:].lstrip("\n")
            return before + "\n\n" + changelog_entry.rstrip("\n") + "\n\n" + after
        return existing_text.rstrip("\n") + "\n\n" + changelog_entry

    first_release = re.search(r"^##\s+\[", existing_text, re.MULTILINE)
    if first_release:
        pos = first_release.start()
        return existing_text[:pos] + changelog_entry + "\n" + existing_text[pos:]

    return existing_text.rstrip("\n") + "\n\n" + changelog_entry


def parse_changelog_versions(changelog_path: Path) -> List[Dict[str, Any]]:
    """Parse version sections from CHANGELOG.md."""
    if not changelog_path.is_file():
        return []
    text = changelog_path.read_text(encoding="utf-8", errors="replace")
    matches = list(CHANGELOG_HEADING_RE.finditer(text))
    sections: List[Dict[str, Any]] = []
    for idx, m in enumerate(matches):
        ver = m.group(1).strip()
        ver_date = m.group(2)
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        sections.append({"version": ver, "date": ver_date, "body": body})
    return sections


def generate_release_notes(
    root: Path,
    version: str,
    *,
    changelog_rel: str = "CHANGELOG.md",
    updates_rel: str = "PROJECT_UPDATES.md",
) -> Dict[str, Any]:
    """Generate clean release notes for a specific version from CHANGELOG.md and PROJECT_UPDATES.md."""
    changelog_path = root / changelog_rel
    updates_path = root / updates_rel

    cl_sections = parse_changelog_versions(changelog_path)
    cl_match = next((s for s in cl_sections if s["version"] == version), None)

    pu_entries = parse_project_updates(updates_path)
    pu_match = next((e for e in pu_entries if e["version"] == version), None)

    if not cl_match and not pu_match:
        return {
            "found": False,
            "version": version,
            "markdown": "",
            "message": f"No release record found for version {version} in {changelog_rel} or {updates_rel}.",
        }

    lines = [f"# Release v{version}", ""]
    if cl_match and cl_match["date"]:
        lines.append(f"Release Date: {cl_match['date']}")
        lines.append("")
    elif pu_match and pu_match["date"]:
        lines.append(f"Release Date: {pu_match['date']}")
        lines.append("")

    if pu_match and pu_match.get("summary"):
        lines.extend(["## Summary", "", pu_match["summary"], ""])

    if cl_match and cl_match["body"]:
        lines.extend(["## Changes", "", cl_match["body"], ""])
    elif pu_match and pu_match.get("changes"):
        lines.extend(["## Changes", ""])
        for item in pu_match["changes"]:
            lines.append(f"- {item}")
        lines.append("")

    if pu_match:
        meta_lines = []
        if pu_match.get("compatibility"):
            meta_lines.append(f"- **Compatibility:** {pu_match['compatibility']}")
        if pu_match.get("migration_required"):
            meta_lines.append(f"- **Migration Required:** {pu_match['migration_required']}")
        if meta_lines:
            lines.extend(["## Upgrade Notes", "", *meta_lines, ""])

    return {
        "found": True,
        "version": version,
        "markdown": "\n".join(lines).strip() + "\n",
    }

