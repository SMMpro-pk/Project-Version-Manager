#!/usr/bin/env python3
"""Comprehensive automated test suite for Project Version Manager."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from config_loader import load_config
from detect_version import detect_project
from git_changes import inspect_changes
from history_manager import generate_release_notes, parse_changelog_versions, parse_project_updates
from semver_utils import SemVerError, calculate_next_version, is_valid_semver, parse_semver
from sync_versions import apply_atomic_writes
from validate_version import validate_project
from version_manager import apply_plan, build_plan, build_status, initialize_project


class TestSemVerCalculations(unittest.TestCase):
    def test_standard_bumps(self) -> None:
        v = parse_semver("1.4.2")
        self.assertEqual(str(calculate_next_version(v, "patch")), "1.4.3")
        self.assertEqual(str(calculate_next_version(v, "minor")), "1.5.0")
        self.assertEqual(str(calculate_next_version(v, "major")), "2.0.0")
        self.assertEqual(str(calculate_next_version(v, "none")), "1.4.2")

    def test_pre_1_0_0_policies(self) -> None:
        v = parse_semver("0.4.2")
        self.assertEqual(
            str(calculate_next_version(v, "major", zero_major_policy="breaking_to_minor")),
            "0.5.0",
        )
        self.assertEqual(
            str(calculate_next_version(v, "minor", zero_major_policy="breaking_to_minor")),
            "0.5.0",
        )
        self.assertEqual(
            str(calculate_next_version(v, "patch", zero_major_policy="breaking_to_minor")),
            "0.4.3",
        )
        self.assertEqual(
            str(calculate_next_version(v, "major", zero_major_policy="strict_semver")),
            "1.0.0",
        )

    def test_prerelease_and_build_metadata(self) -> None:
        base = parse_semver("1.2.0")
        alpha1 = calculate_next_version(base, "minor", pre_id="alpha")
        self.assertEqual(str(alpha1), "1.3.0-alpha.1")
        alpha2 = calculate_next_version(alpha1, "prerelease", pre_id="alpha")
        self.assertEqual(str(alpha2), "1.3.0-alpha.2")
        beta1 = calculate_next_version(alpha2, "prerelease", pre_id="beta")
        self.assertEqual(str(beta1), "1.3.0-beta.1")
        rc1 = calculate_next_version(beta1, "prerelease", pre_id="rc", build="build.105")
        self.assertEqual(str(rc1), "1.3.0-rc.1+build.105")
        stable = calculate_next_version(rc1, "patch")
        self.assertEqual(str(stable), "1.3.0")

        # Precedence checks per SemVer 2.0.0
        self.assertLess(alpha1, alpha2)
        self.assertLess(alpha2, beta1)
        self.assertLess(beta1, rc1)
        self.assertLess(rc1, stable)
        self.assertTrue(parse_semver("1.2.0+build.1").precedence_equals(parse_semver("1.2.0+build.2")))

    def test_invalid_versions(self) -> None:
        for bad in ("1.2", "v1.2.3", "01.2.3", "1.2.3.4", "1.2.3-", "abc", ""):
            self.assertFalse(is_valid_semver(bad), f"Expected invalid SemVer: {bad}")
            with self.assertRaises(SemVerError):
                parse_semver(bad)


class TestVersionDetection(unittest.TestCase):
    def test_detect_version_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "VERSION").write_text("2.3.4\n", encoding="utf-8")
            det = detect_project(root)
            self.assertEqual(det["current_version"], "2.3.4")
            self.assertEqual(det["authoritative_source"]["path"], "VERSION")
            self.assertEqual(det["conflicts"], [])

    def test_detect_package_json_ignores_dependency_versions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(
                json.dumps(
                    {
                        "name": "my-app",
                        "version": "1.2.0",
                        "dependencies": {"react": "18.2.0", "express": "4.19.0"},
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            det = detect_project(root)
            self.assertEqual(det["current_version"], "1.2.0")
            self.assertIn("react", det["ecosystems"])
            self.assertIn("express", det["ecosystems"])

    def test_detect_pyproject_cargo_composer_and_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(
                '[project]\nname = "demo"\nversion = "0.8.0"\ndependencies = ["httpx==0.27.0"]\n',
                encoding="utf-8",
            )
            pkg_dir = root / "src" / "demo"
            pkg_dir.mkdir(parents=True)
            (pkg_dir / "__init__.py").write_text('__version__ = "0.7.9"\n', encoding="utf-8")

            det = detect_project(root)
            self.assertEqual(det["current_version"], "0.8.0")
            self.assertEqual(len(det["conflicts"]), 1)
            self.assertEqual(det["conflicts"][0]["type"], "sync_mismatch")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Cargo.toml").write_text(
                '[package]\nname = "mycrate"\nversion = "1.0.0"\n\n[dependencies]\nserde = "1.0.210"\n',
                encoding="utf-8",
            )
            det = detect_project(root)
            self.assertEqual(det["current_version"], "1.0.0")
            self.assertIn("rust", det["ecosystems"])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "composer.json").write_text(
                json.dumps({"name": "acme/pkg", "version": "3.1.0"}),
                encoding="utf-8",
            )
            det = detect_project(root)
            self.assertEqual(det["current_version"], "3.1.0")
            self.assertIn("composer", det["ecosystems"])

        # Java Maven pom.xml (must ignore parent & dependency versions)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pom.xml").write_text(
                "<project>\n"
                "  <parent><groupId>org.springframework.boot</groupId><version>3.2.0</version></parent>\n"
                "  <artifactId>demo</artifactId>\n"
                "  <version>1.2.3</version>\n"
                "  <dependencies><dependency><version>2.0.0</version></dependency></dependencies>\n"
                "</project>\n",
                encoding="utf-8",
            )
            det = detect_project(root)
            self.assertEqual(det["current_version"], "1.2.3")
            plan = build_plan(root, bump="patch", summary="Fix maven service.")
            apply_plan(root, plan, explicit_user_request=True)
            pom_after = (root / "pom.xml").read_text(encoding="utf-8")
            self.assertIn("<version>1.2.4</version>", pom_after)
            self.assertIn("<version>3.2.0</version>", pom_after)
            self.assertIn("<version>2.0.0</version>", pom_after)

        # Flutter pubspec.yaml & .NET csproj
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pubspec.yaml").write_text("name: flutter_app\nversion: 2.1.0+42\n", encoding="utf-8")
            det = detect_project(root)
            self.assertEqual(det["current_version"], "2.1.0+42")
            self.assertIn("dart", det["ecosystems"])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "App.csproj").write_text("<Project><PropertyGroup><Version>1.0.0</Version></PropertyGroup></Project>\n", encoding="utf-8")
            det = detect_project(root)
            self.assertEqual(det["current_version"], "1.0.0")
            self.assertIn("dotnet", det["ecosystems"])

    def test_detect_authoritative_conflict_and_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text('{"name":"a","version":"1.0.0"}', encoding="utf-8")
            (root / "composer.json").write_text('{"name":"a/b","version":"2.0.0"}', encoding="utf-8")
            det = detect_project(root)
            self.assertIsNone(det["current_version"])
            self.assertTrue(any(c["type"] == "authoritative_mismatch" for c in det["conflicts"]))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "index.html").write_text("<h1>Hello</h1>", encoding="utf-8")
            det = detect_project(root)
            self.assertIsNone(det["current_version"])
            self.assertEqual(det["version_sources"], [])


class TestWorkflowHistoryAndSafety(unittest.TestCase):
    def _create_python_project(self, root: Path) -> None:
        (root / "pyproject.toml").write_text(
            '[project]\nname = "demo"\nversion = "1.0.0"\n',
            encoding="utf-8",
        )
        pkg = root / "src" / "demo"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text('__version__ = "1.0.0"\n', encoding="utf-8")
        (pkg / "core.py").write_text("def greet():\n    return 'hello'\n", encoding="utf-8")

    def test_plan_apply_history_prepend_and_duplicate_prevention(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._create_python_project(root)

            # First update: 1.0.0 -> 1.1.0 (minor)
            plan1 = build_plan(
                root,
                bump="minor",
                category="New Feature",
                summary="Added greeting customization.",
                changes_list=["Added optional name parameter to greet()."],
                affected_areas=["Core API"],
                changelog_sections={"Added": ["Optional name parameter in greet()."]},
                testing="Passed.",
                entry_date="2026-11-09",
            )
            res1 = apply_plan(root, plan1, explicit_user_request=True)
            self.assertTrue(res1["applied"])
            self.assertEqual(res1["next_version"], "1.1.0")
            self.assertIn('version = "1.1.0"', (root / "pyproject.toml").read_text(encoding="utf-8"))
            self.assertIn('__version__ = "1.1.0"', (root / "src" / "demo" / "__init__.py").read_text(encoding="utf-8"))

            # Duplicate attempt without file changes must be rejected
            dup_plan = build_plan(root, bump="patch", summary="Should be blocked by fingerprint.")
            with self.assertRaises(ValueError) as ctx:
                apply_plan(root, dup_plan, explicit_user_request=True)
            self.assertIn("Duplicate update prevented", str(ctx.exception))

            # Modify source code, then apply patch 1.1.0 -> 1.1.1
            (root / "src" / "demo" / "core.py").write_text(
                "def greet(name='world'):\n    return f'hello {name}'\n",
                encoding="utf-8",
            )
            plan2 = build_plan(
                root,
                bump="patch",
                category="Bug Fix",
                summary="Fixed default greeting fallback.",
                changes_list=["Defaulted empty name argument safely."],
                affected_areas=["Core API"],
                changelog_sections={"Fixed": ["Defaulted empty name argument safely."]},
                testing="Passed.",
                entry_date="2026-11-10",
            )
            res2 = apply_plan(root, plan2, explicit_user_request=True)
            self.assertTrue(res2["applied"])
            self.assertEqual(res2["next_version"], "1.1.1")

            # Verify newest entry is prepended at top and older entry is preserved
            updates = parse_project_updates(root / "PROJECT_UPDATES.md")
            self.assertEqual([u["version"] for u in updates], ["1.1.1", "1.1.0"])

            cl_sections = parse_changelog_versions(root / "CHANGELOG.md")
            self.assertEqual([s["version"] for s in cl_sections], ["Unreleased", "1.1.1", "1.1.0"])

            notes = generate_release_notes(root, "1.1.1")
            self.assertTrue(notes["found"])
            self.assertIn("Fixed default greeting fallback.", notes["markdown"])

    def test_no_version_bump_records_unreleased_note_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._create_python_project(root)
            plan = build_plan(
                root,
                bump="none",
                category="Documentation",
                summary="Reorganized internal developer notes.",
                changes_list=["Cleaned up inline comments."],
            )
            res = apply_plan(root, plan, explicit_user_request=True)
            self.assertEqual(res["next_version"], "1.0.0")
            self.assertIn('version = "1.0.0"', (root / "pyproject.toml").read_text(encoding="utf-8"))
            self.assertFalse((root / "CHANGELOG.md").exists())
            updates_text = (root / "PROJECT_UPDATES.md").read_text(encoding="utf-8")
            self.assertIn("Unreleased Update (v1.0.0)", updates_text)

    def test_dry_run_and_suggest_mode_do_not_modify_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._create_python_project(root)
            (root / ".versioning.json").write_text(
                json.dumps({"schema_version": 1, "mode": "suggest"}),
                encoding="utf-8",
            )
            plan = build_plan(root, bump="patch", summary="Fix typo in error message.")
            dry = apply_plan(root, plan, dry_run=True, explicit_user_request=True)
            self.assertTrue(dry["dry_run"])
            self.assertIn('version = "1.0.0"', (root / "pyproject.toml").read_text(encoding="utf-8"))
            self.assertFalse((root / "PROJECT_UPDATES.md").exists())

            with self.assertRaises(ValueError):
                apply_plan(root, plan, dry_run=False, explicit_user_request=True)

    def test_invalid_config_and_failing_test_command_abort_safely(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._create_python_project(root)
            (root / ".versioning.json").write_text(
                json.dumps({"schema_version": 1, "mode": "invalid_mode"}),
                encoding="utf-8",
            )
            _, _, errs, _ = load_config(root)
            self.assertTrue(errs)
            val = validate_project(root)
            self.assertFalse(val["valid"])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._create_python_project(root)
            (root / ".versioning.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "mode": "auto",
                        "require_tests_before_release": True,
                        "test_command": f'"{sys.executable}" -c "import sys; sys.exit(1)"',
                    }
                ),
                encoding="utf-8",
            )
            plan = build_plan(root, bump="patch", summary="Attempted release with failing tests.")
            with self.assertRaises(ValueError) as ctx:
                apply_plan(root, plan, explicit_user_request=True)
            self.assertIn("Pre-release tests failed", str(ctx.exception))
            self.assertIn('version = "1.0.0"', (root / "pyproject.toml").read_text(encoding="utf-8"))

    def test_atomic_rollback_preserves_originals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            f1 = root / "VERSION"
            f2 = root / "secondary.txt"
            f1.write_text("1.0.0\n", encoding="utf-8")
            f2.write_text("original\n", encoding="utf-8")

            with mock.patch("sync_versions.os.replace", side_effect=[None, OSError("disk error")]):
                with self.assertRaises(OSError):
                    apply_atomic_writes({f1: "1.1.0\n", f2: "modified\n"})

            self.assertEqual(f1.read_text(encoding="utf-8"), "1.0.0\n")
            self.assertEqual(f2.read_text(encoding="utf-8"), "original\n")

    def test_init_and_wordpress_sync_and_git_repo_integration(self) -> None:
        # 1. Init unversioned project
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("print('hi')\n", encoding="utf-8")
            dry = initialize_project(root, dry_run=True)
            self.assertTrue(dry["dry_run"])
            self.assertFalse((root / "VERSION").exists())

            init_res = initialize_project(root, initial_version="0.1.0", with_config=True)
            self.assertTrue(init_res["initialized"])
            self.assertEqual((root / "VERSION").read_text(encoding="utf-8").strip(), "0.1.0")
            self.assertTrue((root / ".versioning.json").is_file())

        # 2. WordPress sync preserves unrelated defines & updates header + readme
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "my-plugin.php").write_text(
                "<?php\n/**\n * Plugin Name: My Plugin\n * Version: 1.0.0\n */\n",
                encoding="utf-8",
            )
            (root / "readme.txt").write_text(
                "=== My Plugin ===\nStable tag: 1.0.0\n",
                encoding="utf-8",
            )
            plan = build_plan(root, bump="minor", summary="Added Gutenberg block.")
            apply_plan(root, plan, explicit_user_request=True)
            self.assertIn("Version: 1.1.0", (root / "my-plugin.php").read_text(encoding="utf-8"))
            self.assertIn("Stable tag: 1.1.0", (root / "readme.txt").read_text(encoding="utf-8"))

        # 3. Git repository integration (tag, commit, working tree change)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-b", "main"], cwd=str(root), check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test Bot"], cwd=str(root), check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(root), check=True)
            (root / "package.json").write_text('{"name":"g","version":"1.0.0"}\n', encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=str(root), check=True)
            subprocess.run(["git", "commit", "-m", "Initial release"], cwd=str(root), check=True, capture_output=True)
            subprocess.run(["git", "tag", "v1.0.0"], cwd=str(root), check=True)
            (root / "feature.js").write_text("export const x = 1;\n", encoding="utf-8")

            analysis = inspect_changes(root)
            self.assertTrue(analysis["git_available"])
            self.assertEqual(analysis["latest_semver_tag"], "v1.0.0")
            self.assertFalse(analysis["working_tree"]["clean"])
            self.assertIn("feature.js", analysis["working_tree"]["untracked"])

            status = build_status(root)
            self.assertTrue(status["ready_for_release"])
            self.assertEqual(status["current_version"], "1.0.0")

    def test_cli_entrypoint_smoke(self) -> None:
        cli = str(SCRIPT_DIR / "version_manager.py")
        help_proc = subprocess.run([sys.executable, cli, "--help"], capture_output=True, text=True, check=False)
        self.assertEqual(help_proc.returncode, 0)
        self.assertIn("Project Version Manager CLI", help_proc.stdout)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "VERSION").write_text("1.0.0\n", encoding="utf-8")
            val_proc = subprocess.run(
                [sys.executable, cli, "validate", str(root), "--json"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(val_proc.returncode, 0)
            payload = json.loads(val_proc.stdout)
            self.assertTrue(payload["valid"])


if __name__ == "__main__":
    unittest.main()
