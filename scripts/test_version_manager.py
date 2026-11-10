#!/usr/bin/env python3
"""Comprehensive automated test suite for Project Version Manager."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from config_loader import load_config
from detect_version import detect_project
from git_changes import inspect_changes
from history_manager import generate_release_notes, parse_changelog_versions, parse_project_updates
from semver_utils import SemVerError, calculate_next_version, infer_bump_type, is_valid_semver, parse_semver
from sync_versions import apply_atomic_writes
from validate_version import validate_project
from version_manager import (
    apply_plan,
    build_plan,
    build_status,
    compare_versions,
    initialize_project,
    sync_project,
)


class TestSemVerCalculations(unittest.TestCase):
    def test_standard_bumps_and_infer(self) -> None:
        v = parse_semver("1.4.2")
        self.assertEqual(str(calculate_next_version(v, "patch")), "1.4.3")
        self.assertEqual(str(calculate_next_version(v, "minor")), "1.5.0")
        self.assertEqual(str(calculate_next_version(v, "major")), "2.0.0")
        self.assertEqual(str(calculate_next_version(v, "none")), "1.4.2")

        self.assertEqual(infer_bump_type(v, parse_semver("2.0.0")), "major")
        self.assertEqual(infer_bump_type(v, parse_semver("1.5.0")), "minor")
        self.assertEqual(infer_bump_type(v, parse_semver("1.4.3")), "patch")
        self.assertEqual(infer_bump_type(v, parse_semver("1.4.2-alpha.1")), "prerelease")

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


class TestVersionDetectionAndEcosystems(unittest.TestCase):
    def test_detect_version_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "VERSION").write_text("2.3.4\n", encoding="utf-8")
            det = detect_project(root)
            self.assertEqual(det["current_version"], "2.3.4")
            self.assertEqual(det["authoritative_source"]["path"], "VERSION")
            self.assertEqual(det["conflicts"], [])

    def test_detect_and_sync_package_lock_preserving_unicode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(
                json.dumps(
                    {
                        "name": "my-app",
                        "version": "1.2.0",
                        "description": "Café dashboard",
                        "dependencies": {"react": "18.2.0", "express": "4.19.0"},
                    },
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "package-lock.json").write_text(
                json.dumps(
                    {
                        "name": "my-app",
                        "version": "1.2.0",
                        "lockfileVersion": 3,
                        "packages": {
                            "": {"name": "my-app", "version": "1.2.0", "note": "Café"},
                            "node_modules/react": {"version": "18.2.0"},
                        },
                    },
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            det = detect_project(root)
            self.assertEqual(det["current_version"], "1.2.0")
            self.assertIn("react", det["ecosystems"])
            plan = build_plan(root, bump="minor", summary="Added theme switcher.")
            apply_plan(root, plan, explicit_user_request=True)
            lock_text = (root / "package-lock.json").read_text(encoding="utf-8")
            lock_data = json.loads(lock_text)
            self.assertEqual(lock_data["version"], "1.3.0")
            self.assertEqual(lock_data["packages"][""]["version"], "1.3.0")
            self.assertEqual(lock_data["packages"]["node_modules/react"]["version"], "18.2.0")
            self.assertIn("Café", lock_text)

    def test_detect_and_sync_cargo_lock_setup_cfg_gradle_maven_ios_dotnet(self) -> None:
        # Rust Cargo.toml + Cargo.lock
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Cargo.toml").write_text(
                '[package]\nname = "mycrate"\nversion = "1.0.0"\n\n[dependencies]\nserde = "1.0.210"\n',
                encoding="utf-8",
            )
            (root / "Cargo.lock").write_text(
                '[[package]]\nname = "mycrate"\nversion = "1.0.0"\n\n'
                '[[package]]\nname = "serde"\nversion = "1.0.210"\n',
                encoding="utf-8",
            )
            plan = build_plan(root, bump="patch", summary="Fix parser.")
            apply_plan(root, plan, explicit_user_request=True)
            lock_text = (root / "Cargo.lock").read_text(encoding="utf-8")
            self.assertIn('name = "mycrate"\nversion = "1.0.1"', lock_text)
            self.assertIn('name = "serde"\nversion = "1.0.210"', lock_text)

        # Java Maven pom.xml (with XML comment + parent + dependency)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pom.xml").write_text(
                "<project>\n"
                "  <!-- <version>9.9.9</version> -->\n"
                "  <parent><groupId>org.springframework.boot</groupId><version>3.2.0</version></parent>\n"
                "  <artifactId>demo</artifactId>\n"
                "  <version>1.2.3</version>\n"
                "  <dependencies><dependency><version>2.0.0</version></dependency></dependencies>\n"
                "</project>\n",
                encoding="utf-8",
            )
            plan = build_plan(root, bump="patch", summary="Fix maven service.")
            apply_plan(root, plan, explicit_user_request=True)
            pom_after = (root / "pom.xml").read_text(encoding="utf-8")
            self.assertIn("<version>1.2.4</version>", pom_after)
            self.assertIn("<version>3.2.0</version>", pom_after)
            self.assertIn("<version>2.0.0</version>", pom_after)
            self.assertIn("<!-- <version>9.9.9</version> -->", pom_after)

        # Nested .NET csproj + setup.cfg + build.gradle.kts + iOS Info.plist & podspec
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cs_dir = root / "src" / "MyApp"
            cs_dir.mkdir(parents=True)
            (cs_dir / "MyApp.csproj").write_text(
                "<Project><PropertyGroup><Version>1.0.0</Version></PropertyGroup></Project>\n",
                encoding="utf-8",
            )
            det = detect_project(root)
            self.assertEqual(det["current_version"], "1.0.0")
            self.assertIn("dotnet", det["ecosystems"])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Info.plist").write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                '<plist version="1.0"><dict>'
                '<key>CFBundleShortVersionString</key><string>2.0.0</string>'
                '</dict></plist>\n',
                encoding="utf-8",
            )
            plan = build_plan(root, bump="minor", summary="Added widget.")
            apply_plan(root, plan, explicit_user_request=True)
            self.assertIn("<string>2.1.0</string>", (root / "Info.plist").read_text(encoding="utf-8"))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "setup.cfg").write_text("[metadata]\nname = demo\nversion = 0.2.0\n", encoding="utf-8")
            plan = build_plan(root, bump="patch", summary="Fix packaging.")
            apply_plan(root, plan, explicit_user_request=True)
            self.assertIn("version = 0.2.1", (root / "setup.cfg").read_text(encoding="utf-8"))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "build.gradle.kts").write_text('group = "com.example"\nversion = "1.0.0"\n', encoding="utf-8")
            plan = build_plan(root, bump="minor", summary="Added endpoint.")
            apply_plan(root, plan, explicit_user_request=True)
            self.assertIn('version = "1.1.0"', (root / "build.gradle.kts").read_text(encoding="utf-8"))

    def test_cmake_blocks_prerelease_safely(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.20)\nproject(demo VERSION 1.0.0 LANGUAGES CXX)\n",
                encoding="utf-8",
            )
            plan = build_plan(root, bump="minor", pre_id="alpha", summary="Alpha prep.")
            self.assertEqual(plan["file_edits"][0]["action"], "skip")
            self.assertIn("CMake", plan["file_edits"][0]["reason"])

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


class TestSecurityWorkflowHistoryAndCli(unittest.TestCase):
    def _create_python_project(self, root: Path) -> None:
        (root / "pyproject.toml").write_text(
            '[project]\nname = "demo"\nversion = "1.0.0"\n',
            encoding="utf-8",
        )
        pkg = root / "src" / "demo"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text('__version__ = "1.0.0"\n', encoding="utf-8")
        (pkg / "core.py").write_text("def greet():\n    return 'hello'\n", encoding="utf-8")

    def test_path_traversal_in_config_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "VERSION").write_text("1.0.0\n", encoding="utf-8")
            (root / ".versioning.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "version_file": "../../outside.txt",
                        "update_history": "/tmp/evil.md",
                        "sync_files": ["../secret.py"],
                    }
                ),
                encoding="utf-8",
            )
            _, _, errors, _ = load_config(root)
            self.assertEqual(len(errors), 3)
            val = validate_project(root)
            self.assertFalse(val["valid"])

    def test_untrusted_test_command_requires_explicit_trust(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._create_python_project(root)
            (root / ".versioning.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "mode": "auto",
                        "require_tests_before_release": True,
                        "test_command": f'"{sys.executable}" -c "print(\'ok\')"',
                    }
                ),
                encoding="utf-8",
            )
            plan = build_plan(root, bump="patch", summary="Patch without trusting test_command.")
            with self.assertRaises(ValueError) as ctx:
                apply_plan(root, plan, explicit_user_request=True)
            self.assertIn("requires explicit trust", str(ctx.exception))

            # Passing trust_test_command=True executes and passes
            res = apply_plan(root, plan, trust_test_command=True, explicit_user_request=True)
            self.assertTrue(res["applied"])
            self.assertIn("Passed.", (root / "PROJECT_UPDATES.md").read_text(encoding="utf-8"))

        # Failing test_command aborts cleanly
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
                apply_plan(root, plan, trust_test_command=True, explicit_user_request=True)
            self.assertIn("Pre-release tests failed", str(ctx.exception))
            self.assertIn('version = "1.0.0"', (root / "pyproject.toml").read_text(encoding="utf-8"))

    def test_plan_apply_history_bold_summary_promote_unreleased_and_compare(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._create_python_project(root)
            (root / "CHANGELOG.md").write_text(
                "# Changelog\n\n## [Unreleased]\n\n### Added\n- Pre-staged changelog bullet.\n",
                encoding="utf-8",
            )

            plan1 = build_plan(
                root,
                bump="minor",
                category="New Feature",
                summary="Added greeting customization.\n**Note:** Optional name parameter.",
                changes_list=["Added optional name parameter to greet()."],
                affected_areas=["Core API"],
                changelog_sections={"Added": ["Optional name parameter in greet()."]},
                testing="Passed.",
                entry_date="2026-11-09",
                promote_unreleased=True,
            )
            res1 = apply_plan(root, plan1, explicit_user_request=True)
            self.assertTrue(res1["applied"])
            self.assertEqual(res1["next_version"], "1.1.0")
            cl_text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
            self.assertIn("Pre-staged changelog bullet.", cl_text)
            # [Unreleased] should now be cleared
            cl_sections = parse_changelog_versions(root / "CHANGELOG.md")
            self.assertEqual(cl_sections[0]["version"], "Unreleased")
            self.assertEqual(cl_sections[0]["body"], "")

            # Bold markdown inside Summary should not truncate parsing
            updates = parse_project_updates(root / "PROJECT_UPDATES.md")
            self.assertIn("**Note:** Optional name parameter.", updates[0]["summary"])

            # Duplicate attempt without file changes must be rejected
            dup_plan = build_plan(root, bump="patch", summary="Should be blocked by fingerprint.")
            with self.assertRaises(ValueError) as ctx:
                apply_plan(root, dup_plan, explicit_user_request=True)
            self.assertIn("Duplicate update prevented", str(ctx.exception))

            # Modify source code, then apply target-version 2.0.0 without --bump (inferred major!)
            (root / "src" / "demo" / "core.py").write_text(
                "def greet_v2(name):\n    return f'hello {name}'\n",
                encoding="utf-8",
            )
            plan2 = build_plan(
                root,
                bump=None,
                target_version="2.0.0",
                summary="Replaced greet() with greet_v2().",
                testing="Passed.",
                entry_date="2026-11-10",
            )
            self.assertEqual(plan2["bump"], "major")
            apply_plan(root, plan2, explicit_user_request=True)

            cmp_res = compare_versions(root)
            self.assertEqual(cmp_res["from_version"], "1.1.0")
            self.assertEqual(cmp_res["to_version"], "2.0.0")
            self.assertEqual(cmp_res["bump_type"], "major")

            notes = generate_release_notes(root, "2.0.0")
            self.assertTrue(notes["found"])
            self.assertIn("Replaced greet() with greet_v2().", notes["markdown"])

    def test_wordpress_constant_sync_custom_sync_files_and_sync_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "my-plugin.php").write_text(
                "<?php\n/**\n * Plugin Name: My Plugin\n * Version: 1.2.0\n */\n"
                "define( 'MY_PLUGIN_VERSION', '1.0.0' );\n",
                encoding="utf-8",
            )
            (root / "readme.txt").write_text(
                "=== My Plugin ===\nStable tag: 1.0.0\n",
                encoding="utf-8",
            )
            (root / "constants.py").write_text('__version__ = "1.0.0"\n', encoding="utf-8")
            (root / ".versioning.json").write_text(
                json.dumps({"schema_version": 1, "sync_files": ["constants.py"]}),
                encoding="utf-8",
            )

            # Conflicts detected before sync
            det = detect_project(root)
            self.assertEqual(det["current_version"], "1.2.0")
            self.assertEqual(len(det["conflicts"]), 3)

            # sync aligns PHP constant, readme.txt, and custom sync_files target
            sync_res = sync_project(root)
            self.assertTrue(sync_res["synced"])
            self.assertTrue(sync_res["post_validation"]["valid"])
            self.assertIn("define( 'MY_PLUGIN_VERSION', '1.2.0' );", (root / "my-plugin.php").read_text(encoding="utf-8"))
            self.assertIn("Stable tag: 1.2.0", (root / "readme.txt").read_text(encoding="utf-8"))
            self.assertIn('__version__ = "1.2.0"', (root / "constants.py").read_text(encoding="utf-8"))

            # Subsequent minor bump updates all of them together
            plan = build_plan(root, bump="minor", summary="Added Gutenberg block.")
            apply_plan(root, plan, explicit_user_request=True)
            self.assertIn("Version: 1.3.0", (root / "my-plugin.php").read_text(encoding="utf-8"))
            self.assertIn("define( 'MY_PLUGIN_VERSION', '1.3.0' );", (root / "my-plugin.php").read_text(encoding="utf-8"))
            self.assertIn("Stable tag: 1.3.0", (root / "readme.txt").read_text(encoding="utf-8"))
            self.assertIn('__version__ = "1.3.0"', (root / "constants.py").read_text(encoding="utf-8"))

    def test_no_version_bump_and_dry_run_and_suggest_mode_and_rollback(self) -> None:
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
            self.assertFalse((root / "CHANGELOG.md").exists())
            self.assertIn("Unreleased Update (v1.0.0)", (root / "PROJECT_UPDATES.md").read_text(encoding="utf-8"))

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
            with self.assertRaises(ValueError):
                apply_plan(root, plan, dry_run=False, explicit_user_request=True)

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

    def test_git_gitignore_cross_branch_tags_docs_classification_and_opt_in_commit_tag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-b", "main"], cwd=str(root), check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test Bot"], cwd=str(root), check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(root), check=True)
            (root / ".gitignore").write_text("ignored_logs/\n", encoding="utf-8")
            (root / "package.json").write_text('{"name":"g","version":"1.0.0"}\n', encoding="utf-8")
            (root / "README.md").write_text("# Demo\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=str(root), check=True)
            subprocess.run(["git", "commit", "-m", "Initial release"], cwd=str(root), check=True, capture_output=True)
            subprocess.run(["git", "tag", "v1.0.0"], cwd=str(root), check=True)

            # Create a higher tag on a separate branch; main should ignore it!
            subprocess.run(["git", "checkout", "-b", "feature-x"], cwd=str(root), check=True, capture_output=True)
            (root / "experimental.js").write_text("export const e = 1;\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=str(root), check=True)
            subprocess.run(["git", "commit", "-m", "Exp"], cwd=str(root), check=True, capture_output=True)
            subprocess.run(["git", "tag", "v9.0.0-alpha.1"], cwd=str(root), check=True)
            subprocess.run(["git", "checkout", "main"], cwd=str(root), check=True, capture_output=True)

            # Create a gitignored file + modify README.md (docs-only)
            (root / "ignored_logs").mkdir()
            (root / "ignored_logs" / "app.log").write_text("ignore me\n", encoding="utf-8")
            (root / "README.md").write_text("# Demo\nUpdated docs.\n", encoding="utf-8")

            analysis = inspect_changes(root)
            self.assertTrue(analysis["git_available"])
            self.assertTrue(analysis["gitignore_aware"])
            self.assertEqual(analysis["latest_semver_tag"], "v1.0.0")
            self.assertTrue(analysis["change_classification"]["docs_only"])

            fp_before_log = analysis["fingerprint"]
            (root / "ignored_logs" / "app.log").write_text("more ignored logs\n", encoding="utf-8")
            self.assertEqual(inspect_changes(root)["fingerprint"], fp_before_log)

            # Apply with explicit opt-in --git-commit / --git-tag
            (root / "feature.js").write_text("export const x = 1;\n", encoding="utf-8")
            plan = build_plan(root, bump="minor", summary="Added feature x.")
            res = apply_plan(root, plan, explicit_user_request=True, git_commit=True, git_tag=True)
            self.assertTrue(res["git_actions"]["commit_created"])
            self.assertTrue(res["git_actions"]["tag_created"])
            self.assertEqual(res["git_actions"]["tag_name"], "v1.1.0")

    def test_package_imports_and_cli_end_to_end(self) -> None:
        # 1. Verify scripts.* package imports work from repository root
        imp_proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "import scripts.detect_version, scripts.validate_version, scripts.version_manager",
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(imp_proc.returncode, 0, imp_proc.stderr)

        # 2. Verify end-to-end CLI workflow (init -> plan --out -> apply --plan -> history -> release-notes -> compare)
        cli = str(SCRIPT_DIR / "version_manager.py")
        help_proc = subprocess.run([sys.executable, cli, "--help"], capture_output=True, text=True, check=False)
        self.assertEqual(help_proc.returncode, 0)
        self.assertIn("sync", help_proc.stdout)
        self.assertIn("compare", help_proc.stdout)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("print('hi')\n", encoding="utf-8")
            subprocess.run([sys.executable, cli, "init", str(root)], check=True, capture_output=True)
            (root / "app.py").write_text("print('hello world')\n", encoding="utf-8")
            plan_file = root / "plan.json"
            subprocess.run(
                [
                    sys.executable,
                    cli,
                    "plan",
                    str(root),
                    "--bump",
                    "minor",
                    "--summary",
                    "Updated greeting.",
                    "--out",
                    str(plan_file),
                ],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [sys.executable, cli, "apply", str(root), "--plan", str(plan_file)],
                check=True,
                capture_output=True,
            )
            notes_proc = subprocess.run(
                [sys.executable, cli, "release-notes", str(root), "--version", "0.2.0"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(notes_proc.returncode, 0)
            self.assertIn("Updated greeting.", notes_proc.stdout)


if __name__ == "__main__":
    unittest.main()
