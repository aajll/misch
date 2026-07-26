"""Authoritative configuration schema policy shared by loading and init."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RuntimeDefaults:
    scope: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    db_source: str = "existing"
    platform: str = "unix64"
    defines: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class InitDefaults:
    scope: tuple[str, ...] = ("src/",)
    exclude: tuple[str, ...] = ("tests/", "subprojects/")
    db_source: str = "meson"
    platform: str = "unix64"
    rule_texts: str = "${MISRA_RULE_TEXTS}"


RUNTIME_DEFAULTS = RuntimeDefaults()
INIT_DEFAULTS = InitDefaults()
VALID_DB_SOURCES = frozenset({"meson", "cmake", "existing"})

_PROFILE_FIELDS = {
    "project": {"scope", "exclude"},
    "db": {"source", "path"},
    "platform": {"preset", "xml"},
    "toolchain": {"defines"},
    "rules": {"texts"},
    "report": {"outputs"},
    "baseline": {"path"},
    "deviations": {"suppressions"},
}
_PROFILE_LIST_FIELDS = {
    ("project", "scope"),
    ("project", "exclude"),
    ("toolchain", "defines"),
    ("report", "outputs"),
}


def strip_append(key: str) -> tuple[str, bool]:
    """Return a profile leaf's target key and append intent."""
    if key.startswith("append_"):
        return key[len("append_") :], True
    return key, False


def profile_section_supported(section: str) -> bool:
    """Whether a TOML section may occur in a profile overlay."""
    return section in _PROFILE_FIELDS


def profile_field(section: str, key: str) -> tuple[str, str] | None:
    """Return a supported profile field, or ``None`` for an unknown one."""
    actual_key, _ = strip_append(key)
    if actual_key not in _PROFILE_FIELDS.get(section, set()):
        return None
    return section, actual_key


def profile_allows_append(field: tuple[str, str]) -> bool:
    return field in _PROFILE_LIST_FIELDS


def valid_profile_value(field: tuple[str, str], value: object, *, append: bool) -> bool:
    if field not in _PROFILE_LIST_FIELDS:
        return isinstance(value, str)
    values = value if isinstance(value, list) else [value]
    if not isinstance(value, list) and not append:
        return False
    if field == ("report", "outputs"):
        return all(
            isinstance(item, str)
            or (
                isinstance(item, dict)
                and isinstance(item.get("format"), str)
                and ("path" not in item or isinstance(item["path"], str))
            )
            for item in values
        )
    return all(isinstance(item, str) for item in values)


def profile_value_description(field: tuple[str, str], *, append: bool) -> str:
    if field == ("report", "outputs"):
        return (
            "a report output or list of report outputs"
            if append
            else "a list of report outputs"
        )
    if field in _PROFILE_LIST_FIELDS:
        return "a string or list of strings" if append else "a list of strings"
    return "a string"


class ProfileError(ValueError):
    """Raised when a profile contains invalid keys, values, or merge conflicts."""


def _profile_error(profile_name: str, path: str, message: str) -> None:
    raise ProfileError(f"profile {profile_name!r}: {path} {message}")


def validate_profile(profile: dict, profile_name: str) -> None:
    """Reject profile keys and values outside the supported overlay schema.

    Raises ``ProfileError`` for unknown sections, unsupported fields,
    invalid value types, or append prefixes on non-list targets.
    """
    for section, values in profile.items():
        if not profile_section_supported(section):
            path = (
                f"{section}.{next(iter(values))}"
                if isinstance(values, dict) and values
                else section
            )
            _profile_error(profile_name, path, "is not a supported setting")
        if section == "platform" and isinstance(values, str):
            continue  # Legacy scalar platform form remains supported.
        if not isinstance(values, dict):
            _profile_error(profile_name, section, "must be a TOML table")

        for key, value in values.items():
            _, is_append = strip_append(key)
            path = f"{section}.{key}"
            field = profile_field(section, key)
            if field is None:
                _profile_error(profile_name, path, "is not a supported setting")
            if is_append and not profile_allows_append(field):
                _profile_error(profile_name, path, "can only target a list setting")
            if not valid_profile_value(field, value, append=is_append):
                expected = profile_value_description(field, append=is_append)
                _profile_error(
                    profile_name, path, f"must be {expected}"
                )


def merge(base_data: dict, profile: dict, *, profile_name: str) -> None:
    """Merge *profile* into *base_data* in-place.

    Supports an ``append_`` prefix on leaf keys to extend lists rather than
    replace them.  For example::

        [profiles.aarch64]
        platform.xml = "analysis/aarch64_platform.xml"
        toolchain.append_defines = ["ARCH_ARM64"]
        project.append_exclude = ["generated/"]

    Scalar values are replaced normally, ``toolchain.defines`` and
    ``project.exclude`` are appended to.  The top-level ``platform`` table is
    replaced wholesale rather than deep-merged, because ``preset`` and ``xml``
    are mutually exclusive: a profile's platform fully supersedes the base's.

    After merging, validates that the resulting platform section does not have
    both ``preset`` and ``xml`` set simultaneously.

    Raises ``ProfileError`` for unsupported append targets, non-list keys,
    or conflicting platform selectors after merge.
    """
    prefix = f"profile {profile_name!r}: "
    _deep_merge(base_data, profile, profile_name=profile_name)

    # Validate merged platform: preset and xml must not coexist.
    merged_platform = base_data.get("platform", {})
    if isinstance(merged_platform, dict):
        if "xml" in merged_platform and "preset" in merged_platform:
            raise ProfileError(
                f"{prefix}platform: set either 'preset' or 'xml', not both"
            )


def _deep_merge(
    target: dict,
    patch: dict,
    *,
    profile_name: str | None = None,
    path: tuple[str, ...] = (),
) -> None:
    """Recursively merge *patch* into *target* in-place.

    Internal helper for :func:`merge`.
    """
    prefix = f"profile {profile_name!r}: " if profile_name else ""
    for k, v in patch.items():
        current_path = (*path, k)
        if path == () and k == "platform":
            target[k] = v  # xml XOR preset: replace, never merge two selectors.
            continue
        real_key, is_append = strip_append(k)
        if is_append:
            target_path = (*path, real_key)
            if not profile_allows_append(target_path):
                raise ProfileError(
                    f"{prefix}{'.'.join(current_path)}: unsupported list target"
                )
            if real_key not in target:
                target[real_key] = []
            if not isinstance(target[real_key], list):
                raise ProfileError(
                    f"{prefix}{'.'.join(current_path)}: key {real_key!r} is not a list"
                )
            if isinstance(v, list):
                target[real_key].extend(v)
            else:
                target[real_key].append(v)
        elif isinstance(v, dict):
            if k not in target or not isinstance(target[k], dict):
                target[k] = {}
            _deep_merge(
                target[k], v, profile_name=profile_name, path=current_path
            )
        else:
            target[k] = v
