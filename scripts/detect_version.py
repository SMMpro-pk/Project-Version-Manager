#!/usr/bin/env python3
"""Intelligent project type and version detection across common software ecosystems."""

from __future__ import annotations

import argparse
import configparser
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - fallback for Python <3.11
    tomllib = None  # type: ignore[assignment]

from config_loader import load_config
from semver_utils import is_valid_semver, parse_semver

SKIP_DIRS = {
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
}


@dataclass
class VersionSource:
    """Represents a detected version declaration in a project file."""

    path: str
    file_type: str
    role: str  # "authoritative" | "dependent" | "lockfile"
    raw_version: str
    semver_valid: bool
    normalized_version: Optional[str]
    ecosystem: str
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        try:
            return path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            return None


def _parse_toml_section_value(text: str, sections: Tuple[str, ...], key: str) -> Optional[str]:
    """Extract a simple key = 'val' from specific TOML table sections without tomllib."""
    if tomllib is not None:
        try:
            data = tomllib.loads(text)
            for section in sections:
                curr: Any = data
                found = True
                for part in section.split("."):
                    if isinstance(curr, dict) and part in curr:
                        curr = curr[part]
                    else:
                        found = False
                        break
                if found and isinstance(curr, dict) and isinstance(curr.get(key), str):
                    return curr[key]
        except Exception:
            pass

    current_section = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        sec_match = re.match(r"^\[([^\]]+)\]$", stripped)
        if sec_match:
            current_section = sec_match.group(1).strip()
            continue
        if current_section in sections:
            kv_match = re.match(rf"^{re.escape(key)}\s*=\s*(['\"])([^'\"]+)\1", stripped)
            if kv_match:
                return kv_match.group(2).strip()
    return None


def _detect_ecosystems(root: Path) -> List[str]:
    ecosystems: List[str] = []

    def add(name: str) -> None:
        if name not in ecosystems:
            ecosystems.append(name)

    pkg_json = root / "package.json"
    if pkg_json.is_file():
        add("node")
        try:
            data = json.loads(pkg_json.read_text(encoding="utf-8"))
            deps = {}
            for key in ("dependencies", "devDependencies", "peerDependencies"):
                if isinstance(data.get(key), dict):
                    deps.update(data[key])
            if "next" in deps:
                add("nextjs")
            if "react" in deps:
                add("react")
            if "react-native" in deps:
                add("react-native")
            if "vue" in deps:
                add("vue")
            if "@angular/core" in deps:
                add("angular")
            if "express" in deps:
                add("express")
            if "electron" in deps:
                add("electron")
        except Exception:
            pass

    if (root / "pyproject.toml").is_file() or (root / "setup.cfg").is_file() or (root / "setup.py").is_file():
        add("python")
        py_text = _read_text(root / "pyproject.toml") or ""
        req_text = _read_text(root / "requirements.txt") or ""
        combined = (py_text + "\n" + req_text).lower()
        if "django" in combined:
            add("django")
        if "fastapi" in combined:
            add("fastapi")
        if "flask" in combined:
            add("flask")

    if (root / "Cargo.toml").is_file():
        add("rust")

    if (root / "composer.json").is_file():
        add("php")
        add("composer")
        comp_text = (_read_text(root / "composer.json") or "").lower()
        if "laravel/framework" in comp_text:
            add("laravel")

    if (root / "pom.xml").is_file():
        add("java-maven")
        pom_text = (_read_text(root / "pom.xml") or "").lower()
        if "spring-boot" in pom_text:
            add("spring-boot")

    if (root / "build.gradle").is_file() or (root / "build.gradle.kts").is_file():
        add("java-gradle")
        gradle_text = (
            (_read_text(root / "build.gradle") or "")
            + (_read_text(root / "build.gradle.kts") or "")
        ).lower()
        if "spring-boot" in gradle_text or "org.springframework.boot" in gradle_text:
            add("spring-boot")
        if "com.android.application" in gradle_text or "com.android.library" in gradle_text:
            add("android")

    if (root / "app" / "build.gradle").is_file() or (root / "app" / "build.gradle.kts").is_file():
        add("android")

    if list(root.glob("*.csproj")) or (root / "Directory.Build.props").is_file() or list(root.glob("*.sln")):
        add("dotnet")

    if (root / "pubspec.yaml").is_file():
        pub_text = (_read_text(root / "pubspec.yaml") or "").lower()
        add("flutter" if "flutter:" in pub_text else "dart")

    if (root / "go.mod").is_file():
        add("go")

    if (root / "CMakeLists.txt").is_file() or (root / "meson.build").is_file():
        add("c-cpp")

    if (root / "Package.swift").is_file() or list(root.glob("*.xcodeproj")) or list(root.glob("*.podspec")):
        add("ios-swift")

    # WordPress detection
    for php_file in sorted(root.glob("*.php")):
        head = (_read_text(php_file) or "")[:8192]
        if re.search(r"^\s*\*?\s*Plugin Name\s*:", head, re.MULTILINE | re.IGNORECASE):
            add("wordpress-plugin")
            if re.search(r"WC tested up to|WooCommerce", head, re.IGNORECASE):
                add("woocommerce-extension")
            break

    style_css = root / "style.css"
    if style_css.is_file():
        head = (_read_text(style_css) or "")[:8192]
        if re.search(r"^\s*\*?\s*Theme Name\s*:", head, re.MULTILINE | re.IGNORECASE):
            add("wordpress-theme")

    if not ecosystems and ((root / "index.html").is_file() or list(root.glob("*.html"))):
        add("web-static")

    if not ecosystems:
        ecosystems.append("generic")

    return ecosystems


def _make_source(
    root: Path,
    file_path: Path,
    file_type: str,
    role: str,
    raw_version: str,
    ecosystem: str,
    note: str = "",
) -> VersionSource:
    rel = file_path.relative_to(root).as_posix()
    cleaned = raw_version.strip()
    valid = is_valid_semver(cleaned)
    norm = str(parse_semver(cleaned)) if valid else None
    return VersionSource(
        path=rel,
        file_type=file_type,
        role=role,
        raw_version=cleaned,
        semver_valid=valid,
        normalized_version=norm,
        ecosystem=ecosystem,
        note=note,
    )


def _find_python_init_files(root: Path) -> List[Path]:
    candidates: List[Path] = []
    for base in (root, root / "src"):
        if not base.is_dir():
            continue
        for init_file in sorted(base.glob("*/__init__.py")):
            if not any(part in SKIP_DIRS for part in init_file.parts):
                candidates.append(init_file)
        for ver_file in sorted(base.glob("*/_version.py")):
            if not any(part in SKIP_DIRS for part in ver_file.parts):
                candidates.append(ver_file)
    return candidates


def _get_latest_git_semver_tag(root: Path) -> Optional[str]:
    try:
        proc = subprocess.run(
            ["git", "tag", "--list"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if proc.returncode != 0:
            return None
        parsed_tags: List[Tuple[Any, str]] = []
        for line in proc.stdout.splitlines():
            tag = line.strip()
            if not tag:
                continue
            if is_valid_semver(tag, strip_v=True):
                parsed_tags.append((parse_semver(tag, strip_v=True), tag))
        if not parsed_tags:
            return None
        parsed_tags.sort(key=lambda item: item[0], reverse=True)
        return parsed_tags[0][1]
    except Exception:
        return None


def detect_project(project_root: Path) -> Dict[str, Any]:
    """Detect ecosystems, version declarations, authoritative source, and conflicts."""
    root = project_root.resolve()
    config, cfg_warnings, cfg_errors, cfg_path = load_config(root)
    ecosystems = _detect_ecosystems(root)
    sources: List[VersionSource] = []
    notes: List[str] = []

    # 1. package.json & package-lock.json
    pkg_json = root / "package.json"
    if pkg_json.is_file():
        text = _read_text(pkg_json)
        if text:
            try:
                data = json.loads(text)
                if isinstance(data, dict) and isinstance(data.get("version"), str):
                    sources.append(
                        _make_source(root, pkg_json, "package.json", "authoritative", data["version"], "node")
                    )
            except json.JSONDecodeError:
                notes.append("Could not parse package.json as valid JSON.")

    pkg_lock = root / "package-lock.json"
    if pkg_lock.is_file():
        text = _read_text(pkg_lock)
        if text:
            try:
                data = json.loads(text)
                lock_ver = None
                if isinstance(data, dict):
                    packages = data.get("packages")
                    if isinstance(packages, dict) and isinstance(packages.get(""), dict):
                        lock_ver = packages[""].get("version")
                    if lock_ver is None:
                        lock_ver = data.get("version")
                if isinstance(lock_ver, str):
                    sources.append(
                        _make_source(
                            root,
                            pkg_lock,
                            "package-lock.json",
                            "lockfile",
                            lock_ver,
                            "node",
                            "Generated lockfile root version",
                        )
                    )
            except json.JSONDecodeError:
                notes.append("Could not parse package-lock.json as valid JSON.")

    # 2. pyproject.toml & setup.cfg & __init__.py
    pyproject = root / "pyproject.toml"
    has_pyproject_ver = False
    if pyproject.is_file():
        text = _read_text(pyproject) or ""
        ver = _parse_toml_section_value(text, ("project", "tool.poetry"), "version")
        if ver:
            has_pyproject_ver = True
            sources.append(
                _make_source(root, pyproject, "pyproject.toml", "authoritative", ver, "python")
            )

    setup_cfg = root / "setup.cfg"
    if setup_cfg.is_file():
        parser = configparser.ConfigParser()
        try:
            parser.read(setup_cfg, encoding="utf-8")
            if parser.has_option("metadata", "version"):
                val = parser.get("metadata", "version").strip()
                if val and not val.startswith("attr:"):
                    role = "dependent" if has_pyproject_ver else "authoritative"
                    sources.append(
                        _make_source(root, setup_cfg, "setup.cfg", role, val, "python")
                    )
        except Exception:
            notes.append("Could not parse setup.cfg.")

    for init_file in _find_python_init_files(root):
        text = _read_text(init_file) or ""
        match = re.search(r'^__version__\s*=\s*(["\'])([^"\']+)\1', text, re.MULTILINE)
        if match:
            role = "dependent" if (has_pyproject_ver or setup_cfg.is_file()) else "authoritative"
            sources.append(
                _make_source(root, init_file, "python_module_version", role, match.group(2), "python")
            )

    # 3. Cargo.toml & Cargo.lock
    cargo_toml = root / "Cargo.toml"
    cargo_pkg_name: Optional[str] = None
    if cargo_toml.is_file():
        text = _read_text(cargo_toml) or ""
        cargo_pkg_name = _parse_toml_section_value(text, ("package",), "name")
        ver = _parse_toml_section_value(text, ("package",), "version")
        if ver:
            sources.append(
                _make_source(root, cargo_toml, "Cargo.toml", "authoritative", ver, "rust")
            )

    cargo_lock = root / "Cargo.lock"
    if cargo_lock.is_file() and cargo_pkg_name:
        text = _read_text(cargo_lock) or ""
        pattern = re.compile(
            r'\[\[package\]\]\s*\nname\s*=\s*"'
            + re.escape(cargo_pkg_name)
            + r'"\s*\nversion\s*=\s*"([^"]+)"',
            re.MULTILINE,
        )
        match = pattern.search(text)
        if match:
            sources.append(
                _make_source(
                    root,
                    cargo_lock,
                    "Cargo.lock",
                    "lockfile",
                    match.group(1),
                    "rust",
                    "Generated lockfile crate version",
                )
            )

    # 4. composer.json & composer.lock
    composer_json = root / "composer.json"
    has_composer_ver = False
    if composer_json.is_file():
        text = _read_text(composer_json)
        if text:
            try:
                data = json.loads(text)
                if isinstance(data, dict) and isinstance(data.get("version"), str):
                    has_composer_ver = True
                    sources.append(
                        _make_source(root, composer_json, "composer.json", "authoritative", data["version"], "php")
                    )
            except json.JSONDecodeError:
                notes.append("Could not parse composer.json.")

    # 5. pubspec.yaml
    pubspec = root / "pubspec.yaml"
    if pubspec.is_file():
        text = _read_text(pubspec) or ""
        match = re.search(r"^version:\s*([^\s#]+)", text, re.MULTILINE)
        if match:
            sources.append(
                _make_source(root, pubspec, "pubspec.yaml", "authoritative", match.group(1), "dart-flutter")
            )

    # 6. pom.xml
    pom_xml = root / "pom.xml"
    if pom_xml.is_file():
        text = _read_text(pom_xml) or ""
        try:
            xml_root = ET.fromstring(text)
            for child in xml_root:
                tag = child.tag.split("}")[-1]
                if tag == "version" and child.text and child.text.strip():
                    sources.append(
                        _make_source(root, pom_xml, "pom.xml", "authoritative", child.text.strip(), "java-maven")
                    )
                    break
        except ET.ParseError:
            notes.append("Could not parse pom.xml.")

    # 7. build.gradle / build.gradle.kts
    for gradle_name in ("build.gradle", "build.gradle.kts", "app/build.gradle", "app/build.gradle.kts"):
        gradle_path = root / gradle_name
        if gradle_path.is_file():
            text = _read_text(gradle_path) or ""
            m_ver = re.search(r'^\s*version\s*=?\s*(["\'])([^"\']+)\1', text, re.MULTILINE)
            if m_ver:
                sources.append(
                    _make_source(root, gradle_path, "gradle", "authoritative", m_ver.group(2), "java-gradle")
                )
            else:
                m_android = re.search(r'^\s*versionName\s*=?\s*(["\'])([^"\']+)\1', text, re.MULTILINE)
                if m_android:
                    sources.append(
                        _make_source(root, gradle_path, "gradle_android", "authoritative", m_android.group(2), "android")
                    )

    # 8. .NET Directory.Build.props and *.csproj
    props_file = root / "Directory.Build.props"
    if props_file.is_file():
        text = _read_text(props_file) or ""
        m = re.search(r"<Version>\s*([^<\s]+)\s*</Version>", text)
        if m:
            sources.append(
                _make_source(root, props_file, "Directory.Build.props", "authoritative", m.group(1), "dotnet")
            )

    for csproj in sorted(root.glob("*.csproj")):
        text = _read_text(csproj) or ""
        m = re.search(r"<Version>\s*([^<\s]+)\s*</Version>", text)
        if m:
            role = "dependent" if props_file.is_file() else "authoritative"
            sources.append(
                _make_source(root, csproj, "csproj", role, m.group(1), "dotnet")
            )

    # 9. CMakeLists.txt
    cmake = root / "CMakeLists.txt"
    if cmake.is_file():
        text = _read_text(cmake) or ""
        m = re.search(r"project\s*\([^)]*\bVERSION\s+([0-9]+\.[0-9]+(?:\.[0-9]+)?)", text, re.IGNORECASE | re.DOTALL)
        if m:
            sources.append(
                _make_source(root, cmake, "CMakeLists.txt", "authoritative", m.group(1), "c-cpp")
            )

    # 10. WordPress plugin / theme / readme.txt
    has_wp_header = False
    for php_file in sorted(root.glob("*.php")):
        head = (_read_text(php_file) or "")[:8192]
        if re.search(r"^\s*\*?\s*Plugin Name\s*:", head, re.MULTILINE | re.IGNORECASE):
            m_ver = re.search(r"^\s*\*?\s*Version\s*:\s*([^\r\n]+)", head, re.MULTILINE | re.IGNORECASE)
            if m_ver:
                has_wp_header = True
                sources.append(
                    _make_source(root, php_file, "wordpress_plugin_header", "authoritative", m_ver.group(1), "wordpress")
                )
            break

    style_css = root / "style.css"
    if style_css.is_file():
        head = (_read_text(style_css) or "")[:8192]
        if re.search(r"^\s*\*?\s*Theme Name\s*:", head, re.MULTILINE | re.IGNORECASE):
            m_ver = re.search(r"^\s*\*?\s*Version\s*:\s*([^\r\n]+)", head, re.MULTILINE | re.IGNORECASE)
            if m_ver:
                has_wp_header = True
                sources.append(
                    _make_source(root, style_css, "wordpress_theme_header", "authoritative", m_ver.group(1), "wordpress")
                )

    readme_txt = root / "readme.txt"
    if readme_txt.is_file():
        head = (_read_text(readme_txt) or "")[:8192]
        m_stable = re.search(r"^\s*Stable tag\s*:\s*([^\r\n]+)", head, re.MULTILINE | re.IGNORECASE)
        if m_stable and m_stable.group(1).strip().lower() != "trunk":
            role = "dependent" if has_wp_header else "authoritative"
            sources.append(
                _make_source(root, readme_txt, "wordpress_readme", role, m_stable.group(1), "wordpress")
            )

    # 11. VERSION / version.txt / configured version_file
    has_manifest_auth = any(s.role == "authoritative" for s in sources)
    version_candidates = [config["version_file"], "VERSION", "version.txt"]
    seen_ver_files = set()
    for rel_name in version_candidates:
        if rel_name in seen_ver_files:
            continue
        seen_ver_files.add(rel_name)
        v_path = root / rel_name
        if v_path.is_file():
            content = (_read_text(v_path) or "").strip()
            if content and len(content.splitlines()) == 1:
                role = "dependent" if has_manifest_auth else "authoritative"
                sources.append(
                    _make_source(root, v_path, "version_file", role, content, "generic")
                )

    # Apply explicit authoritative_file override from .versioning.json if configured
    if config.get("authoritative_file"):
        override = config["authoritative_file"]
        matched_override = False
        for src in sources:
            if src.path == override:
                src.role = "authoritative"
                matched_override = True
            elif src.role == "authoritative":
                src.role = "dependent"
        if not matched_override:
            notes.append(
                f"Configured authoritative_file '{override}' was not found among detected version sources."
            )

    # Resolve authoritative version & check conflicts
    auth_sources = [s for s in sources if s.role == "authoritative"]
    dep_sources = [s for s in sources if s.role == "dependent"]
    lock_sources = [s for s in sources if s.role == "lockfile"]

    conflicts: List[Dict[str, Any]] = []
    auth_versions = {s.raw_version for s in auth_sources}
    if len(auth_versions) > 1:
        conflicts.append(
            {
                "type": "authoritative_mismatch",
                "message": "Multiple authoritative version sources disagree.",
                "sources": [{"path": s.path, "version": s.raw_version} for s in auth_sources],
            }
        )

    current_version: Optional[str] = None
    authoritative_source: Optional[Dict[str, Any]] = None
    if len(auth_sources) == 1:
        authoritative_source = auth_sources[0].to_dict()
        current_version = auth_sources[0].raw_version
    elif len(auth_sources) > 1 and len(auth_versions) == 1:
        authoritative_source = auth_sources[0].to_dict()
        current_version = auth_sources[0].raw_version

    if current_version is not None:
        for dep in dep_sources + lock_sources:
            if dep.raw_version != current_version:
                conflicts.append(
                    {
                        "type": "sync_mismatch",
                        "message": (
                            f"{dep.path} ({dep.role}) has version '{dep.raw_version}', "
                            f"which differs from authoritative version '{current_version}'."
                        ),
                        "path": dep.path,
                        "role": dep.role,
                        "found_version": dep.raw_version,
                        "expected_version": current_version,
                    }
                )

    git_tag = _get_latest_git_semver_tag(root)
    if current_version is None and not sources:
        notes.append(
            "No version declaration found. Use 'init' to initialize version tracking safely."
        )
    if "go" in ecosystems and current_version is None and git_tag:
        notes.append(
            f"Go module detected with Git tag '{git_tag}'. Consider initializing a VERSION file or using Git tags."
        )

    return {
        "project_root": str(root),
        "ecosystems": ecosystems,
        "config_path": str(cfg_path) if cfg_path else None,
        "config": config,
        "config_warnings": cfg_warnings,
        "config_errors": cfg_errors,
        "current_version": current_version,
        "current_version_valid_semver": bool(current_version and is_valid_semver(current_version)),
        "authoritative_source": authoritative_source,
        "version_sources": [s.to_dict() for s in sources],
        "conflicts": conflicts,
        "git_latest_tag": git_tag,
        "notes": notes,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Detect project ecosystems, version declarations, and conflicts."
    )
    parser.add_argument("path", nargs="?", default=".", help="Path to the project root (default: .)")
    parser.add_argument("--json", action="store_true", help="Output result as JSON")
    args = parser.parse_args(argv)

    result = detect_project(Path(args.path))
    if args.json:
        print(json.dumps(result, indent=2))
        return 0 if not result["config_errors"] else 1

    print(f"Project Root : {result['project_root']}")
    print(f"Ecosystems   : {', '.join(result['ecosystems'])}")
    print(f"Current Ver  : {result['current_version'] or 'Not detected'}")
    if result["git_latest_tag"]:
        print(f"Latest GitTag: {result['git_latest_tag']}")
    print("\nDetected Version Sources:")
    if not result["version_sources"]:
        print("  (none)")
    for src in result["version_sources"]:
        validity = "valid SemVer" if src["semver_valid"] else "NON-SEMVER"
        print(f"  - [{src['role']}] {src['path']} -> {src['raw_version']} ({validity})")
    if result["conflicts"]:
        print("\nConflicts:")
        for c in result["conflicts"]:
            print(f"  ! {c['message']}")
    if result["notes"]:
        print("\nNotes:")
        for n in result["notes"]:
            print(f"  * {n}")
    return 0 if not result["config_errors"] else 1


if __name__ == "__main__":
    sys.exit(main())

