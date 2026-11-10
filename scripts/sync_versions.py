#!/usr/bin/env python3
"""Safe, format-preserving, atomic version synchronization across project files."""

from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class PlannedEdit:
    """Represents a planned or skipped version update in a single file."""

    path: str
    file_type: str
    role: str
    old_version: str
    new_version: str
    action: str  # "update" | "skip"
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _replace_package_json(text: str, new_version: str) -> str:
    data_before = json.loads(text)
    # Match top-level "version": "..." (indent 1-4 spaces typically, first occurrence at shallowest indent)
    pattern = re.compile(r'^([ \t]*"version"\s*:\s*)"([^"]+)"', re.MULTILINE)
    matches = list(pattern.finditer(text))
    if not matches:
        raise ValueError("Could not locate top-level 'version' key in JSON file.")
    # Pick match with minimal leading indentation (top-level key)
    best = min(matches, key=lambda m: len(m.group(1).split('"')[0]))
    updated = text[: best.start()] + f'{best.group(1)}"{new_version}"' + text[best.end() :]
    data_after = json.loads(updated)
    if data_after.get("version") != new_version:
        raise ValueError("Post-edit JSON verification failed for top-level version.")
    if {k: v for k, v in data_before.items() if k != "version"} != {
        k: v for k, v in data_after.items() if k != "version"
    }:
        raise ValueError("Safety check failed: non-version keys would be altered.")
    return updated


def _replace_package_lock_json(text: str, new_version: str) -> str:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("Invalid package-lock.json structure.")
    if "version" in data:
        data["version"] = new_version
    packages = data.get("packages")
    if isinstance(packages, dict) and isinstance(packages.get(""), dict) and "version" in packages[""]:
        packages[""]["version"] = new_version
    # Detect indentation from original file
    indent_match = re.search(r"^\n?\{\n([ \t]+)\"", text)
    indent = indent_match.group(1) if indent_match else 2
    trailing = "\n" if text.endswith("\n") else ""
    return json.dumps(data, indent=indent) + trailing


def _replace_toml_section_version(text: str, sections: Tuple[str, ...], new_version: str) -> str:
    lines = text.splitlines(keepends=True)
    current_section = ""
    replaced = False
    out_lines: List[str] = []

    for line in lines:
        stripped = line.strip()
        sec_match = re.match(r"^\[([^\]]+)\]$", stripped)
        if sec_match:
            current_section = sec_match.group(1).strip()
            out_lines.append(line)
            continue

        if not replaced and current_section in sections and not stripped.startswith("#"):
            kv_match = re.match(r'^(\s*version\s*=\s*)(["\'])([^"\']+)(\2)(.*)$', line, re.DOTALL)
            if kv_match:
                line = f"{kv_match.group(1)}{kv_match.group(2)}{new_version}{kv_match.group(4)}{kv_match.group(5)}"
                replaced = True
        out_lines.append(line)

    if not replaced:
        raise ValueError(f"Could not find version key in TOML section(s) {sections}.")
    return "".join(out_lines)


def _replace_ini_section_version(text: str, section: str, new_version: str) -> str:
    lines = text.splitlines(keepends=True)
    current_section = ""
    replaced = False
    out_lines: List[str] = []
    for line in lines:
        stripped = line.strip()
        sec_match = re.match(r"^\[([^\]]+)\]$", stripped)
        if sec_match:
            current_section = sec_match.group(1).strip()
            out_lines.append(line)
            continue
        if not replaced and current_section == section and not stripped.startswith(("#", ";")):
            kv_match = re.match(r"^(\s*version\s*=\s*)([^\r\n#;]+)(.*)$", line, re.DOTALL)
            if kv_match:
                line = f"{kv_match.group(1)}{new_version}{kv_match.group(3)}"
                replaced = True
        out_lines.append(line)
    if not replaced:
        raise ValueError(f"Could not find version key in [{section}].")
    return "".join(out_lines)


def _replace_cargo_lock_version(text: str, crate_name: str, new_version: str) -> str:
    pattern = re.compile(
        r'(\[\[package\]\]\s*\nname\s*=\s*"'
        + re.escape(crate_name)
        + r'"\s*\nversion\s*=\s*")([^"]+)(")',
        re.MULTILINE,
    )
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise ValueError(
            f"Expected 1 package entry for '{crate_name}' in Cargo.lock, found {len(matches)}."
        )
    m = matches[0]
    return text[: m.start()] + f"{m.group(1)}{new_version}{m.group(3)}" + text[m.end() :]


def _replace_pom_xml(text: str, old_version: str, new_version: str) -> str:
    # Ensure we only replace the direct <project><version> element, never parent or dependency versions
    xml_root = ET.fromstring(text)
    found_ver = None
    for child in xml_root:
        if child.tag.split("}")[-1] == "version" and child.text:
            found_ver = child.text.strip()
            break
    if found_ver != old_version:
        raise ValueError("pom.xml direct <version> mismatch.")

    # Locate top-level <version> outside <parent> and <dependencies>
    # Strip <parent>...</parent> and <dependencies>...</dependencies> spans from search
    masked = list(text)
    for tag in ("parent", "dependencies", "dependencyManagement", "build", "profiles"):
        for m in re.finditer(rf"<{tag}\b[^>]*>.*?</{tag}>", text, re.DOTALL):
            for i in range(m.start(), m.end()):
                masked[i] = " "
    masked_text = "".join(masked)
    ver_match = re.search(rf"(<version>\s*){re.escape(old_version)}(\s*</version>)", masked_text)
    if not ver_match:
        raise ValueError("Could not safely isolate project <version> in pom.xml.")
    return text[: ver_match.start()] + f"{ver_match.group(1)}{new_version}{ver_match.group(2)}" + text[ver_match.end() :]


def _apply_single_replacement(
    file_path: Path,
    file_type: str,
    old_version: str,
    new_version: str,
    *,
    cargo_crate_name: Optional[str] = None,
) -> str:
    text = file_path.read_text(encoding="utf-8")

    if file_type == "version_file":
        trailing = "\n" if text.endswith("\n") else ""
        return new_version + trailing

    if file_type in ("package.json", "composer.json"):
        return _replace_package_json(text, new_version)

    if file_type == "package-lock.json":
        return _replace_package_lock_json(text, new_version)

    if file_type == "pyproject.toml":
        return _replace_toml_section_version(text, ("project", "tool.poetry"), new_version)

    if file_type == "setup.cfg":
        return _replace_ini_section_version(text, "metadata", new_version)

    if file_type == "Cargo.toml":
        return _replace_toml_section_version(text, ("package",), new_version)

    if file_type == "Cargo.lock":
        if not cargo_crate_name:
            raise ValueError("Cannot safely update Cargo.lock without Cargo.toml package name.")
        return _replace_cargo_lock_version(text, cargo_crate_name, new_version)

    if file_type == "pubspec.yaml":
        updated, count = re.subn(
            r"^(version:\s*)([^\s#]+)",
            rf"\g<1>{new_version}",
            text,
            count=1,
            flags=re.MULTILINE,
        )
        if count != 1:
            raise ValueError("Could not locate top-level version in pubspec.yaml.")
        return updated

    if file_type == "pom.xml":
        return _replace_pom_xml(text, old_version, new_version)

    if file_type == "gradle":
        updated, count = re.subn(
            r'^(\s*version\s*=?\s*["\'])([^"\']+)(["\'])',
            rf"\g<1>{new_version}\g<3>",
            text,
            count=1,
            flags=re.MULTILINE,
        )
        if count != 1:
            raise ValueError("Could not locate version declaration in Gradle build file.")
        return updated

    if file_type == "gradle_android":
        updated, count = re.subn(
            r'^(\s*versionName\s*=?\s*["\'])([^"\']+)(["\'])',
            rf"\g<1>{new_version}\g<3>",
            text,
            count=1,
            flags=re.MULTILINE,
        )
        if count != 1:
            raise ValueError("Could not locate versionName in Android Gradle build file.")
        return updated

    if file_type in ("csproj", "Directory.Build.props"):
        updated, count = re.subn(
            r"(<Version>\s*)([^<\s]+)(\s*</Version>)",
            rf"\g<1>{new_version}\g<3>",
            text,
            count=1,
        )
        if count != 1:
            raise ValueError(f"Could not locate <Version> tag in {file_path.name}.")
        return updated

    if file_type == "python_module_version":
        updated, count = re.subn(
            r'^(__version__\s*=\s*["\'])([^"\']+)(["\'])',
            rf"\g<1>{new_version}\g<3>",
            text,
            count=1,
            flags=re.MULTILINE,
        )
        if count != 1:
            raise ValueError(f"Could not locate __version__ in {file_path.name}.")
        return updated

    if file_type == "CMakeLists.txt":
        updated, count = re.subn(
            r"(project\s*\([^)]*\bVERSION\s+)([0-9]+\.[0-9]+(?:\.[0-9]+)?)",
            rf"\g<1>{new_version}",
            text,
            count=1,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if count != 1:
            raise ValueError("Could not locate project(... VERSION ...) in CMakeLists.txt.")
        return updated

    if file_type in ("wordpress_plugin_header", "wordpress_theme_header"):
        head_limit = min(len(text), 8192)
        head = text[:head_limit]
        updated_head, count = re.subn(
            r"^(\s*\*?\s*Version\s*:\s*)([^\r\n]+)",
            rf"\g<1>{new_version}",
            head,
            count=1,
            flags=re.MULTILINE | re.IGNORECASE,
        )
        if count != 1:
            raise ValueError(f"Could not locate WordPress Version header in {file_path.name}.")
        return updated_head + text[head_limit:]

    if file_type == "wordpress_readme":
        head_limit = min(len(text), 8192)
        head = text[:head_limit]
        updated_head, count = re.subn(
            r"^(\s*Stable tag\s*:\s*)([^\r\n]+)",
            rf"\g<1>{new_version}",
            head,
            count=1,
            flags=re.MULTILINE | re.IGNORECASE,
        )
        if count != 1:
            raise ValueError("Could not locate Stable tag in readme.txt.")
        return updated_head + text[head_limit:]

    raise ValueError(f"Unsupported version file type '{file_type}'.")


def plan_version_sync(
    root: Path,
    sources: List[Dict[str, Any]],
    new_version: str,
    *,
    auto_update_dependent: bool = True,
    extra_sync_files: Optional[List[str]] = None,
) -> Tuple[List[PlannedEdit], Dict[Path, str]]:
    """Prepare planned version edits and compute updated file contents in memory."""
    edits: List[PlannedEdit] = []
    prepared_contents: Dict[Path, str] = {}

    cargo_crate_name: Optional[str] = None
    cargo_toml = root / "Cargo.toml"
    if cargo_toml.is_file():
        m = re.search(r'^name\s*=\s*["\']([^"\']+)["\']', cargo_toml.read_text(encoding="utf-8"), re.MULTILINE)
        if m:
            cargo_crate_name = m.group(1)

    extra_set = {p.replace("\\", "/") for p in (extra_sync_files or [])}

    for src in sources:
        rel_path = src["path"]
        role = src["role"]
        file_type = src["file_type"]
        old_ver = src["raw_version"]
        full_path = root / rel_path

        if old_ver == new_version:
            edits.append(
                PlannedEdit(
                    path=rel_path,
                    file_type=file_type,
                    role=role,
                    old_version=old_ver,
                    new_version=new_version,
                    action="skip",
                    reason="Already at target version.",
                )
            )
            continue

        if role != "authoritative" and not auto_update_dependent and rel_path not in extra_set:
            edits.append(
                PlannedEdit(
                    path=rel_path,
                    file_type=file_type,
                    role=role,
                    old_version=old_ver,
                    new_version=new_version,
                    action="skip",
                    reason="auto_update_dependent_files is false.",
                )
            )
            continue

        try:
            updated_text = _apply_single_replacement(
                full_path,
                file_type,
                old_ver,
                new_version,
                cargo_crate_name=cargo_crate_name,
            )
            prepared_contents[full_path] = updated_text
            edits.append(
                PlannedEdit(
                    path=rel_path,
                    file_type=file_type,
                    role=role,
                    old_version=old_ver,
                    new_version=new_version,
                    action="update",
                )
            )
        except Exception as exc:
            edits.append(
                PlannedEdit(
                    path=rel_path,
                    file_type=file_type,
                    role=role,
                    old_version=old_ver,
                    new_version=new_version,
                    action="skip",
                    reason=f"Safe sync skipped: {exc}",
                )
            )

    return edits, prepared_contents


def apply_atomic_writes(file_contents: Dict[Path, str]) -> None:
    """Write multiple files atomically with full rollback if any write fails."""
    backups: Dict[Path, Optional[bytes]] = {}
    for target in file_contents:
        backups[target] = target.read_bytes() if target.exists() else None

    try:
        for target, content in file_contents.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = target.with_name(f".{target.name}.pvm.tmp")
            tmp_path.write_text(content, encoding="utf-8")
            os.replace(tmp_path, target)
    except Exception:
        # Roll back every modified file
        for target, orig_bytes in backups.items():
            try:
                if orig_bytes is None:
                    if target.exists():
                        target.unlink()
                else:
                    target.write_bytes(orig_bytes)
            except OSError:
                pass
        raise

