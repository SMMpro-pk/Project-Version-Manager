# Supported Ecosystems & Project Manifests

`scripts/detect_version.py` automatically detects project ecosystems and version declarations without modifying files.

## Ecosystem Detection Matrix

| Category | Ecosystems / Frameworks | Primary Version Source(s) | Secondary / Lockfile Targets |
| --- | --- | --- | --- |
| **Web / JS / TS** | HTML/CSS/JS, Node.js, React, Next.js, Vue, Angular, Express, Electron, React Native | `package.json` (`"version"`) | `package-lock.json`, `VERSION` |
| **Python** | Python, Django, Flask, FastAPI | `pyproject.toml` (`[project]`, `[tool.poetry]`, `[tool.flit.metadata]`), `setup.cfg` | `__init__.py` / `_version.py` (`__version__`), `VERSION` |
| **Rust** | Rust / Cargo (crates & workspaces) | `Cargo.toml` (`[package].version`, `[workspace.package].version`) | `Cargo.lock` (matching crate block) |
| **PHP** | PHP, Composer, Laravel | `composer.json` (`"version"`) | `VERSION` |
| **Java / JVM** | Maven, Gradle, Spring Boot | `pom.xml` (direct `<project><version>`), `build.gradle`, `build.gradle.kts` | `VERSION` |
| **C# / .NET** | .NET SDK projects (root & nested `*.csproj`) | `Directory.Build.props`, `*.csproj` (`<Version>`, `<PackageVersion>`, `<VersionPrefix>`) | `VERSION` |
| **Mobile** | Flutter / Dart, Android, React Native, iOS/Swift | `pubspec.yaml` (`version:`), `app/build.gradle*` (`versionName`), `Info.plist` (`CFBundleShortVersionString`), `*.podspec`, `project.pbxproj` (`MARKETING_VERSION`), `package.json` | `VERSION` |
| **Systems / Go** | C / C++, Go | `CMakeLists.txt` (`project(... VERSION ...)` numeric `X.Y.Z` only), `VERSION`, Git tags (`go.mod` ecosystems) | `VERSION` / `version.txt` |
| **CMS / WordPress** | WordPress plugins, WordPress themes, WooCommerce extensions | Main plugin `*.php` (`Version:`), theme `style.css` (`Version:`) | `readme.txt` (`Stable tag:`), `define('*_VERSION', ...)` in main plugin PHP |
| **Generic** | Any repository | `VERSION`, `version.txt`, or custom `version_file` | — |

## What Is Never Modified

- Dependency versions (`dependencies`, `devDependencies`, `peerDependencies`, `[project.dependencies]`, `[dependencies]` in `Cargo.toml`, `<dependencies>` in `pom.xml`).
- Parent POM versions (`<parent><version>`).
- API versions (`/api/v1`), database migration versions, or third-party tool versions.
- `Stable tag: trunk` in WordPress `readme.txt` (preserved untouched unless already pinned to a version).

