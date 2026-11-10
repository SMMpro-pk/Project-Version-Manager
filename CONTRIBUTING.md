# Contributing to Project Version Manager

Thank you for your interest in contributing to **Project Version Manager**!

## Development Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/SMMpro-pk/Project-Version-Manager.git project-version-manager
   cd project-version-manager
   ```
2. Ensure Python 3.10 or newer is installed (`python --version`). No third-party Python packages are required.
3. Run the automated test suite:
   ```bash
   python -m unittest discover -s scripts -p "test_*.py" -v
   ```

## Guidelines

- **Keep dependencies minimal:** All scripts in `scripts/` use only the Python standard library so the skill remains portable across environments.
- **Keep semantic judgment in `SKILL.md` / `references/`:** Deterministic operations (parsing, detection, validation, synchronization, history formatting) belong in `scripts/`; semantic impact analysis belongs in the AI skill instructions.
- **Test all changes:** Add unit and integration tests in `scripts/test_version_manager.py` using isolated temporary directories (`tempfile.TemporaryDirectory`). Never write tests that modify files outside temporary directories.
- **Preserve formatting and safety:** Version synchronization must never touch dependency versions or unrelated files, and dry-run mode must never alter disk state.
- **Separate skill releases from examples:** Do not mix changes to example fixtures in `examples/` with the skill's own release history in `CHANGELOG.md`.

## Pull Request Checklist

- [ ] All tests pass (`python -m unittest discover -s scripts -p "test_*.py" -v`).
- [ ] `python scripts/validate_version.py .` passes cleanly.
- [ ] Documentation in `README.md`, `SKILL.md`, and `references/` reflects any behavior or CLI changes.
- [ ] No credentials, personal paths, or temporary artifacts are included.

