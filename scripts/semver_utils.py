#!/usr/bin/env python3
"""Semantic Versioning 2.0.0 parsing, validation, comparison, and bump calculation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Tuple

SEMVER_PATTERN = re.compile(
    r"^(?P<major>0|[1-9]\d*)"
    r"\.(?P<minor>0|[1-9]\d*)"
    r"\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+(?P<build>[0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)

PRE_ID_PATTERN = re.compile(r"^(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)$")
BUILD_ID_PATTERN = re.compile(r"^[0-9a-zA-Z-]+$")

VALID_BUMPS = ("major", "minor", "patch", "prerelease", "none")
VALID_ZERO_POLICIES = ("breaking_to_minor", "strict_semver")


class SemVerError(ValueError):
    """Raised when a version string or bump specification is invalid."""


@dataclass(frozen=True)
class SemVer:
    """Represents a parsed Semantic Version 2.0.0 value."""

    major: int
    minor: int
    patch: int
    prerelease: Tuple[str, ...] = ()
    build: Tuple[str, ...] = ()

    @property
    def core(self) -> Tuple[int, int, int]:
        return (self.major, self.minor, self.patch)

    @property
    def is_prerelease(self) -> bool:
        return bool(self.prerelease)

    @property
    def is_zero_major(self) -> bool:
        return self.major == 0

    def core_string(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    def without_build(self) -> "SemVer":
        return SemVer(self.major, self.minor, self.patch, self.prerelease, ())

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            base += "-" + ".".join(self.prerelease)
        if self.build:
            base += "+" + ".".join(self.build)
        return base

    def _precedence_key(self) -> Tuple:
        # Per SemVer 2.0.0 section 11: build metadata does not figure into precedence.
        # Normal version has higher precedence than pre-release version.
        pre_flag = 1 if not self.prerelease else 0
        pre_parts = []
        for ident in self.prerelease:
            if ident.isdigit():
                pre_parts.append((0, int(ident), ""))
            else:
                pre_parts.append((1, 0, ident))
        return (self.major, self.minor, self.patch, pre_flag, tuple(pre_parts))

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SemVer):
            return NotImplemented
        return self._precedence_key() < other._precedence_key()

    def __le__(self, other: object) -> bool:
        if not isinstance(other, SemVer):
            return NotImplemented
        return self._precedence_key() <= other._precedence_key()

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, SemVer):
            return NotImplemented
        return self._precedence_key() > other._precedence_key()

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, SemVer):
            return NotImplemented
        return self._precedence_key() >= other._precedence_key()

    def precedence_equals(self, other: "SemVer") -> bool:
        return self._precedence_key() == other._precedence_key()


def parse_semver(version_str: str, *, strip_v: bool = False) -> SemVer:
    """Parse and validate a Semantic Versioning 2.0.0 string."""
    if not isinstance(version_str, str):
        raise SemVerError(f"Version must be a string, got {type(version_str).__name__}.")

    cleaned = version_str.strip()
    if strip_v and cleaned.startswith(("v", "V")):
        cleaned = cleaned[1:]

    match = SEMVER_PATTERN.match(cleaned)
    if not match:
        raise SemVerError(
            f"Invalid Semantic Version '{version_str}'. "
            "Expected MAJOR.MINOR.PATCH[-PRERELEASE][+BUILD] (e.g., 1.2.3 or 1.2.0-beta.1)."
        )

    pre_raw = match.group("prerelease")
    build_raw = match.group("build")

    return SemVer(
        major=int(match.group("major")),
        minor=int(match.group("minor")),
        patch=int(match.group("patch")),
        prerelease=tuple(pre_raw.split(".")) if pre_raw else (),
        build=tuple(build_raw.split(".")) if build_raw else (),
    )


def is_valid_semver(version_str: str, *, strip_v: bool = False) -> bool:
    """Return True if version_str is valid SemVer 2.0.0."""
    try:
        parse_semver(version_str, strip_v=strip_v)
        return True
    except SemVerError:
        return False


def _validate_prerelease_id(pre_id: str) -> Tuple[str, ...]:
    parts = tuple(p for p in pre_id.strip().split(".") if p)
    if not parts:
        raise SemVerError("Prerelease identifier cannot be empty.")
    for part in parts:
        if not PRE_ID_PATTERN.match(part):
            raise SemVerError(f"Invalid prerelease identifier '{part}'.")
    return parts


def _validate_build_metadata(build: str) -> Tuple[str, ...]:
    parts = tuple(p for p in build.strip().split(".") if p)
    if not parts:
        raise SemVerError("Build metadata cannot be empty.")
    for part in parts:
        if not BUILD_ID_PATTERN.match(part):
            raise SemVerError(f"Invalid build metadata identifier '{part}'.")
    return parts


def _increment_prerelease(current_pre: Tuple[str, ...], pre_id: str | None) -> Tuple[str, ...]:
    if pre_id:
        requested = _validate_prerelease_id(pre_id)
        # If requested matches current prefix (e.g. ("alpha",) and current is ("alpha", "1"))
        if (
            len(current_pre) == len(requested) + 1
            and current_pre[: len(requested)] == requested
            and current_pre[-1].isdigit()
        ):
            return requested + (str(int(current_pre[-1]) + 1),)
        if current_pre == requested:
            return requested + ("1",)
        return requested + ("1",) if not requested[-1].isdigit() else requested

    if not current_pre:
        return ("alpha", "1")

    if current_pre[-1].isdigit():
        return current_pre[:-1] + (str(int(current_pre[-1]) + 1),)
    return current_pre + ("1",)


def calculate_next_version(
    current: SemVer,
    bump: str,
    *,
    zero_major_policy: str = "breaking_to_minor",
    pre_id: str | None = None,
    build: str | None = None,
    target_version: str | None = None,
    allow_downgrade: bool = False,
) -> SemVer:
    """Calculate the next SemVer given current version, bump category, and policy."""
    if zero_major_policy not in VALID_ZERO_POLICIES:
        raise SemVerError(
            f"Unknown zero_major_policy '{zero_major_policy}'. "
            f"Allowed: {', '.join(VALID_ZERO_POLICIES)}."
        )

    build_tuple = _validate_build_metadata(build) if build else ()

    if target_version is not None:
        target = parse_semver(target_version)
        if build_tuple and not target.build:
            target = SemVer(target.major, target.minor, target.patch, target.prerelease, build_tuple)
        if not allow_downgrade and target < current:
            raise SemVerError(
                f"Target version '{target}' is lower than current version '{current}'."
            )
        return target

    bump_norm = bump.strip().lower()
    if bump_norm not in VALID_BUMPS:
        raise SemVerError(
            f"Invalid bump type '{bump}'. Allowed: {', '.join(VALID_BUMPS)}."
        )

    if bump_norm == "none":
        if build_tuple:
            return SemVer(current.major, current.minor, current.patch, current.prerelease, build_tuple)
        return current

    if bump_norm == "prerelease":
        if current.is_prerelease:
            next_pre = _increment_prerelease(current.prerelease, pre_id)
            return SemVer(current.major, current.minor, current.patch, next_pre, build_tuple)
        # Starting a prerelease from a stable version defaults to patch + prerelease
        next_pre = _increment_prerelease((), pre_id or "alpha")
        return SemVer(current.major, current.minor, current.patch + 1, next_pre, build_tuple)

    # Handle graduating or bumping from an existing prerelease when no pre_id is requested
    if current.is_prerelease and pre_id is None:
        if bump_norm == "patch":
            return SemVer(current.major, current.minor, current.patch, (), build_tuple)
        if bump_norm == "minor":
            if current.patch == 0:
                return SemVer(current.major, current.minor, 0, (), build_tuple)
            return SemVer(current.major, current.minor + 1, 0, (), build_tuple)
        if bump_norm == "major":
            if current.is_zero_major and zero_major_policy == "breaking_to_minor":
                if current.patch == 0:
                    return SemVer(0, current.minor, 0, (), build_tuple)
                return SemVer(0, current.minor + 1, 0, (), build_tuple)
            if current.minor == 0 and current.patch == 0:
                return SemVer(current.major, 0, 0, (), build_tuple)
            return SemVer(current.major + 1, 0, 0, (), build_tuple)

    # Calculate next core numbers
    if bump_norm == "major":
        if current.is_zero_major and zero_major_policy == "breaking_to_minor":
            major, minor, patch = 0, current.minor + 1, 0
        else:
            major, minor, patch = current.major + 1, 0, 0
    elif bump_norm == "minor":
        major, minor, patch = current.major, current.minor + 1, 0
    else:  # patch
        major, minor, patch = current.major, current.minor, current.patch + 1

    if pre_id:
        next_pre = _increment_prerelease((), pre_id)
        return SemVer(major, minor, patch, next_pre, build_tuple)

    return SemVer(major, minor, patch, (), build_tuple)


def infer_bump_type(current: SemVer, target: SemVer) -> str:
    """Infer semantic bump category when moving from current to target version."""
    if target.core == current.core and target.prerelease == current.prerelease:
        return "none"
    if target.is_prerelease and (target.core == current.core or target.core > current.core):
        if target.core == current.core:
            return "prerelease"
    if target.major != current.major:
        return "major"
    if target.minor != current.minor:
        return "minor"
    if target.patch != current.patch:
        return "patch"
    return "prerelease"


