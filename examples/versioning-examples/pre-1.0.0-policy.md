# Pre-1.0.0 Versioning Examples

When `current_version` has `major == 0` (for example `0.4.2`):

## Default Policy (`breaking_to_minor`)

| Change Impact | Bump | Result |
| --- | --- | --- |
| Breaking API change | `major` | `0.5.0` |
| New feature | `minor` | `0.5.0` |
| Bug fix | `patch` | `0.4.3` |

## Strict SemVer Policy (`strict_semver`)

| Change Impact | Bump | Result |
| --- | --- | --- |
| Breaking API change | `major` | `1.0.0` |
| New feature | `minor` | `0.5.0` |
| Bug fix | `patch` | `0.4.3` |

