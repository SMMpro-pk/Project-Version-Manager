#!/usr/bin/env python3
"""Safe, format-preserving, atomic version synchronization across project files."""

from __future__ import annotations

import json
import os
import plistlib
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]

if __package__:
    from .semver_utils import parse_semver
else:
    from semver_utils import parse_semver


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
    pattern = re.compile(r'^([ \t]*"version"\s*:\s*)"([^"]+)"', re.MULTILINE)
    matches = list(pattern.finditer(text))
    if not matches:
        raise ValueError("Could not locate top-level 'version' key in JSON file.")
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


def _replace_package_lock_json(text: str, old_version: str, new_version: str) -> str:
    """Surgically update root and packages[''] versions in package-lock.json without re-serializing."""
    data_before = json.loads(text)
    if not isinstance(data_before, dict):
        raise ValueError("Invalid package-lock.json structure.")

    updated = text
    # 1. Top-level "version" (shallowest indent)
    if "version" in data_before:
        top_matches = list(re.finditer(r'^([ \t]*"version"\s*:\s*)"([^"]+)"', updated, re.MULTILINE))
        if top_matches:
            best = min(top_matches, key=lambda m: len(m.group(1).split('"')[0]))
            updated = updated[: best.start()] + f'{best.group(1)}"{new_version}"' + updated[best.end() :]

    # 2. packages[""]["version"] in lockfileVersion 2/3
    packages = data_before.get("packages")
    if isinstance(packages, dict) and isinstance(packages.get(""), dict) and "version" in packages[""]:
        root_pkg_re = re.compile(
            r'(""\s*:\s*\{[^{}]*?"version"\s*:\s*)"' + re.escape(old_version) + r'"',
            re.DOTALL,
        )
        m_pkg = root_pkg_re.search(updated)
        if m_pkg:
            updated = updated[: m_pkg.start()] + f'{m_pkg.group(1)}"{new_version}"' + updated[m_pkg.end() :]
        else:
            # Fallback preserving unicode characters if root package block has nested objects before version
            data_before["packages"][""]["version"] = new_version
            if "version" in data_before:
                data_before["version"] = new_version
            indent_match = re.search(r"^\n?\{\n([ \t]+)\"", text)
            indent = indent_match.group(1) if indent_match else 2
            trailing = "\n" if text.endswith("\n") else ""
            return json.dumps(data_before, indent=indent, ensure_ascii=False) + trailing

    data_after = json.loads(updated)
    if "version" in data_after and data_after["version"] != new_version:
        raise ValueError("Post-edit verification failed for package-lock.json top-level version.")
    if isinstance(data_after.get("packages"), dict) and isinstance(data_after["packages"].get(""), dict):
        if "version" in data_after["packages"][""] and data_after["packages"][""]["version"] != new_version:
            raise ValueError("Post-edit verification failed for package-lock.json packages[''] version.")
    return updated


def _replace_toml_section_version(text: str, sections: Tuple[str, ...], new_version: str) -> str:
    lines = text.splitlines(keepends=True)
    current_section = ""
    replaced = False
    in_multiline: Optional[str] = None
    out_lines: List[str] = []

    for line in lines:
        stripped = line.strip()
        if in_multiline is not None:
            if in_multiline in line:
                in_multiline = None
            out_lines.append(line)
            continue

        # Track opening of multi-line string values without closing on same line
        for quote in ('"""', "'''"):
            if quote in line and line.count(quote) % 2 == 1:
                in_multiline = quote
                break

        sec_match = re.match(r"^\[([^\]]+)\]$", stripped)
        if sec_match and in_multiline is None:
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

    result = "".join(out_lines)
    if tomllib is not None:
        tomllib.loads(result)
    return result


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
    xml_root = ET.fromstring(text)
    found_ver = None
    for child in xml_root:
        if child.tag.split("}")[-1] == "version" and child.text:
            found_ver = child.text.strip()
            break
    if found_ver != old_version:
        raise ValueError("pom.xml direct <version> mismatch.")

    masked = list(text)
    # Mask XML comments and non-root container elements
    for m in re.finditer(r"<!--.*?-->", text, re.DOTALL):
        for i in range(m.start(), m.end()):
            masked[i] = " "
    for tag in (
        "parent",
        "dependencies",
        "dependencyManagement",
        "build",
        "profiles",
        "reporting",
        "distributionManagement",
    ):
        for m in re.finditer(rf"<{tag}\b[^>]*>.*?</{tag}>", text, re.DOTALL):
            for i in range(m.start(), m.end()):
                masked[i] = " "
    masked_text = "".join(masked)
    ver_match = re.search(rf"(<version>\s*){re.escape(old_version)}(\s*</version>)", masked_text)
    if not ver_match:
        raise ValueError("Could not safely isolate project <version> in pom.xml.")
    return text[: ver_match.start()] + f"{ver_match.group(1)}{new_version}{ver_match.group(2)}" + text[ver_match.end() :]


def _replace_info_plist(raw_bytes: bytes, new_version: str) -> str:
    plist_data = plistlib.loads(raw_bytes)
    if "CFBundleShortVersionString" not in plist_data:
        raise ValueError("CFBundleShortVersionString not found in Info.plist.")
    text = raw_bytes.decode("utf-8")
    updated, count = re.subn(
        r"(<key>\s*CFBundleShortVersionString\s*</key>\s*<string>)([^<]+)(</string>)",
        rf"\g<1>{new_version}\g<3>",
        text,
        count=1,
        flags=re.DOTALL,
    )
    if count != 1:
        raise ValueError("Could not safely replace CFBundleShortVersionString in XML Info.plist.")
    return updated


def _apply_single_replacement(
    file_path: Path,
    file_type: str,
    old_version: str,
    new_version: str,
    *,
    cargo_crate_name: Optional[str] = None,
    override_text: Optional[str] = None,
) -> str:
    if file_type == "info_plist":
        raw_bytes = override_text.encode("utf-8") if override_text is not None else file_path.read_bytes()
        return _replace_info_plist(raw_bytes, new_version)

    text = override_text if override_text is not None else file_path.read_text(encoding="utf-8")

    if file_type == "version_file":
        trailing = "\n" if text.endswith("\n") else ""
        return new_version + trailing

    if file_type in ("package.json", "composer.json"):
        return _replace_package_json(text, new_version)

    if file_type == "package-lock.json":
        return _replace_package_lock_json(text, old_version, new_version)

    if file_type == "pyproject.toml":
        return _replace_toml_section_version(
            text, ("project", "tool.poetry", "tool.flit.metadata"), new_version
        )

    if file_type == "setup.cfg":
        return _replace_ini_section_version(text, "metadata", new_version)

    if file_type == "Cargo.toml":
        return _replace_toml_section_version(text, ("package", "workspace.package"), new_version)

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
        tag = "Version"
    elif file_type == "csproj_package":
        tag = "PackageVersion"
    elif file_type in ("csproj_prefix", "Directory.Build.props_prefix"):
        tag = "VersionPrefix"
    else:
        tag = ""

    if tag:
        updated, count = re.subn(
            rf"(<{tag}>\s*)([^<\s]+)(\s*</{tag}>)",
            rf"\g<1>{new_version}\g<3>",
            text,
            count=1,
        )
        if count != 1:
            raise ValueError(f"Could not locate <{tag}> tag in {file_path.name}.")
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
        sv = parse_semver(new_version)
        if sv.is_prerelease or sv.build:
            raise ValueError(
                f"CMake project(... VERSION ...) only supports numeric MAJOR.MINOR.PATCH (got '{new_version}')."
            )
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

    if file_type == "podspec":
        updated, count = re.subn(
            r"""^(\s*\w+\.version\s*=\s*['"])([^'"]+)(['"])""",
            rf"\g<1>{new_version}\g<3>",
            text,
            count=1,
            flags=re.MULTILINE,
        )
        if count != 1:
            raise ValueError(f"Could not locate podspec version in {file_path.name}.")
        return updated

    if file_type == "pbxproj_marketing":
        updated, count = re.subn(
            r"^(\s*MARKETING_VERSION\s*=\s*)([^;\s]+)(\s*;)",
            rf"\g<1>{new_version}\g<3>",
            text,
            flags=re.MULTILINE,
        )
        if count < 1:
            raise ValueError(f"Could not locate MARKETING_VERSION in {file_path.name}.")
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

    if file_type == "wordpress_php_constant":
        updated, count = re.subn(
            r"""(define\s*\(\s*['"][A-Z0-9_]*VERSION['"]\s*,\s*['"])([^'"]+)(['"]\s*\))""",
            rf"\g<1>{new_version}\g<3>",
            text,
            count=1,
        )
        if count != 1:
            raise ValueError(f"Could not locate WordPress version constant in {file_path.name}.")
        return updated

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
    existing_contents: Optional[Dict[Path, str]] = None,
) -> Tuple[List[PlannedEdit], Dict[Path, str]]:
    """Prepare planned version edits and compute updated file contents in memory.

    Supports multiple version declarations inside the same file (e.g., WordPress header + constant)
    by chaining edits against existing_contents.
    """
    edits: List[PlannedEdit] = []
    prepared_contents: Dict[Path, str] = dict(existing_contents or {})

    cargo_crate_name: Optional[str] = None
    cargo_toml = root / "Cargo.toml"
    if cargo_toml.is_file():
        m = re.search(r'^name\s*=\s*["\']([^"\']+)["\']', cargo_toml.read_text(encoding="utf-8"), re.MULTILINE)
        if m:
            cargo_crate_name = m.group(1)

    extra_set = {p.replace("\\", "/").lstrip("./") for p in (extra_sync_files or [])}

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
                override_text=prepared_contents.get(full_path),
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
