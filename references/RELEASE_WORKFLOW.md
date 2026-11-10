# End-to-End Release Workflow

## Standard Flow (`auto` mode)

1. **Discover:** `python scripts/version_manager.py detect "<root>" --json`
2. **Review State & Memory:** `python scripts/version_manager.py status "<root>" --json` and `history "<root>"`
3. **Inspect Changes:** `python scripts/version_manager.py analyze "<root>" --json`
   - Read actual source diffs (`git diff` / `git show`) to evaluate semantic impact.
4. **Validate & Test:**
   - Run `python scripts/version_manager.py validate "<root>"`
   - Run the project's automated test suite. Abort if tests fail.
5. **Plan:**
   ```bash
   python scripts/version_manager.py plan "<root>" \
     --bump minor \
     --category "New Feature" \
     --summary "Describe verified changes" \
     --change "Detail 1" \
     --area "Core" \
     --added "User-facing feature 1" \
     --testing "Passed." \
     --out release-plan.json
   ```
6. **Dry Run:** `python scripts/version_manager.py apply "<root>" --plan release-plan.json --dry-run`
7. **Apply:** `python scripts/version_manager.py apply "<root>" --plan release-plan.json`
8. **Release Notes (Optional):** `python scripts/version_manager.py release-notes "<root>"`

## Mode Behavior

- **`auto`:** Applies validated updates after meaningful completed work.
- **`suggest`:** `apply` without `--dry-run` is blocked; present the planned version and rationale to the user.
- **`manual`:** Only apply when the user explicitly provides a bump category or target version.

## Atomic Safety & Rollback

Multi-file version updates and documentation updates use temporary files + `os.replace()` with in-memory backups. If any write fails, all modified files are restored to their original bytes.

