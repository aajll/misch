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
