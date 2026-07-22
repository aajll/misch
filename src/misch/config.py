"""Load and validate `misra.toml`.

Everything that differs between projects is data here, not shell. See
docs/configuration.md for the full schema.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(Exception):
    pass


@dataclass(slots=True)
class Config:
    project_root: Path
    scope: list[str]
    exclude: list[str]
    db_source: str  # meson | cmake | existing
    db_path: str | None
    platform: str  # cppcheck preset name or path to a platform XML
    defines: list[str]
    rule_texts: str | None  # resolved absolute path, or None (BYO absent)
    outputs: list[dict]  # [{"format": "terminal"}, {"format": "json", "path": ...}]
    baseline_path: Path
    suppressions_path: Path | None  # cppcheck project suppressions file, if any

    build_dir: Path = field(init=False)

    def __post_init__(self) -> None:
        self.build_dir = self.project_root / "build_analysis"


# For build systems without native compile-DB export (plain Make), generate
# one with an interceptor such as `bear -- make` and use source = "existing".
_VALID_DB_SOURCES = {"meson", "cmake", "existing"}

# Profile overlays may patch only the documented configuration fields. Keep this
# separate from base-config parsing so existing base configuration remains
# backward-compatible while new profile input gets controlled validation.
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


def load(config_path: Path, profile_name: str | None = None) -> Config:
    config_path = config_path.resolve()
    if not config_path.is_file():
        raise ConfigError(f"config not found: {config_path}")
    root = config_path.parent

    with open(config_path, "rb") as fh:
        data = tomllib.load(fh)

    if profile_name:
        profiles = data.get("profiles", {})
        if not isinstance(profiles, dict):
            raise ConfigError("[profiles] must be a TOML table")
        if profile_name not in profiles:
            raise ConfigError(f"profile not found: {profile_name}")
        profile = profiles[profile_name]
        if not isinstance(profile, dict):
            raise ConfigError(f"profile {profile_name!r} must be a TOML table")
        _validate_profile(profile, profile_name)
        _deep_merge(data, profile, profile_name=profile_name)

    project = data.get("project", {})
    db = data.get("db", {})
    platform = data.get("platform", {})
    toolchain = data.get("toolchain", {})
    rules = data.get("rules", {})
    report = data.get("report", {})
    baseline = data.get("baseline", {})
    deviations = data.get("deviations", {})

    db_source = db.get("source", "existing")
    if db_source not in _VALID_DB_SOURCES:
        raise ConfigError(
            f"db.source must be one of {sorted(_VALID_DB_SOURCES)}, got {db_source!r}"
        )

    # Handle platform as a dict or a string for backward compatibility
    if isinstance(platform, dict):
        plat = (
            str(_configured_path(platform["xml"], root, "platform.xml"))
            if "xml" in platform
            else platform.get("preset") or "unix64"
        )
    else:
        plat = str(platform)

    outputs = report.get("outputs") or [{"format": "terminal"}]
    outputs = [_norm_output(o, root) for o in outputs]

    return Config(
        project_root=root,
        scope=list(project.get("scope", [])),
        exclude=list(project.get("exclude", [])),
        db_source=db_source,
        db_path=db.get("path"),
        platform=plat,
        defines=list(toolchain.get("defines", [])),
        rule_texts=_resolve_rule_texts(rules.get("texts"), root),
        outputs=outputs,
        baseline_path=_resolve_path(baseline.get("path"), root, "misra-baseline.json"),
        suppressions_path=_optional_path(deviations.get("suppressions"), root),
    )


def _validate_profile(profile: dict, profile_name: str) -> None:
    """Reject profile keys and values outside the supported overlay schema."""
    for section, values in profile.items():
        if section not in _PROFILE_FIELDS:
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
            is_append = key.startswith("append_")
            actual_key = key[len("append_") :] if is_append else key
            path = f"{section}.{key}"
            if actual_key not in _PROFILE_FIELDS[section]:
                _profile_error(profile_name, path, "is not a supported setting")
            field = (section, actual_key)
            if is_append and field not in _PROFILE_LIST_FIELDS:
                _profile_error(profile_name, path, "can only target a list setting")
            if not _valid_profile_value(field, value, allow_item=is_append):
                expected = _profile_value_description(field, allow_item=is_append)
                _profile_error(profile_name, path, f"must be {expected}")


def _valid_profile_value(
    field: tuple[str, str], value: object, *, allow_item: bool
) -> bool:
    if field in _PROFILE_LIST_FIELDS:
        return _valid_list_value(field, value, allow_item=allow_item)
    return isinstance(value, str)


def _valid_list_value(
    field: tuple[str, str], value: object, *, allow_item: bool
) -> bool:
    values = value if isinstance(value, list) else [value]
    if not isinstance(value, list) and not allow_item:
        return False
    if field == ("report", "outputs"):
        return all(_valid_report_output(item) for item in values)
    return all(isinstance(item, str) for item in values)


def _valid_report_output(value: object) -> bool:
    if isinstance(value, str):
        return True
    if not isinstance(value, dict) or set(value) - {"format", "path"}:
        return False
    if not isinstance(value.get("format"), str):
        return False
    return "path" not in value or isinstance(value["path"], str)


def _profile_value_description(field: tuple[str, str], *, allow_item: bool) -> str:
    if field == ("report", "outputs"):
        return (
            "a report output or list of report outputs"
            if allow_item
            else "a list of report outputs"
        )
    if field in _PROFILE_LIST_FIELDS:
        return "a string or list of strings" if allow_item else "a list of strings"
    return "a string"


def _profile_error(profile_name: str, path: str, message: str) -> None:
    raise ConfigError(f"profile {profile_name!r}: {path} {message}")


def _deep_merge(
    target: dict,
    patch: dict,
    *,
    profile_name: str | None = None,
    path: tuple[str, ...] = (),
) -> None:
    """Recursively merge *patch* into *target* in-place.

    Supports an ``append_`` prefix on leaf keys to extend lists rather than
    replace them.  For example, in a profile:

        [profiles.arm]
        platform.preset = "arm"
        toolchain.append_defines = ["ARCH_ARM"]
        project.append_exclude = ["generated/"]

    This deep-merges the ``platform`` table, replaces scalar values normally,
    and appends to the existing ``toolchain.defines`` and ``project.exclude``
    lists.

    ``append_<key>`` is permitted only for documented list settings. It raises
    ``ConfigError`` for a non-list or unsupported target. If a supported target
    is missing from the base, it is auto-initialised as an empty list.
    """
    prefix = f"profile {profile_name!r}: " if profile_name else ""
    for k, v in patch.items():
        current_path = (*path, k)
        if k.startswith("append_"):
            real_key = k[len("append_") :]
            target_path = (*path, real_key)
            if target_path not in _PROFILE_LIST_FIELDS:
                raise ConfigError(
                    f"{prefix}{'.'.join(current_path)}: unsupported list target"
                )
            if real_key not in target:
                target[real_key] = []
            if not isinstance(target[real_key], list):
                raise ConfigError(
                    f"{prefix}{'.'.join(current_path)}: key {real_key!r} is not a list"
                )
            if isinstance(v, list):
                target[real_key].extend(v)
            else:
                target[real_key].append(v)
        elif isinstance(v, dict):
            if k not in target or not isinstance(target[k], dict):
                target[k] = {}
            _deep_merge(target[k], v, profile_name=profile_name, path=current_path)
        else:
            target[k] = v


def _optional_path(configured: str | None, root: Path) -> Path | None:
    if not configured:
        return None
    p = Path(configured)
    return p if p.is_absolute() else (root / p)


def _resolve_path(configured: str | None, root: Path, default: str) -> Path:
    p = Path(configured) if configured else Path(default)
    return p if p.is_absolute() else (root / p)


def _configured_path(value: object, root: Path, key: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{key} must be a non-empty string, got {value!r}")
    return _resolve_path(value, root, "")


def _norm_output(o: object, root: Path) -> dict:
    if isinstance(o, str):
        return {"format": o}
    if isinstance(o, dict) and "format" in o:
        output = dict(o)
        if "path" in output:
            output["path"] = str(
                _configured_path(output["path"], root, "report.outputs[].path")
            )
        return output
    raise ConfigError(f"invalid report output entry: {o!r}")


def _resolve_rule_texts(configured: str | None, root: Path) -> str | None:
    """Precedence: $MISRA_RULE_TEXTS  >  misra.toml [rules].texts.

    Returns an absolute path if a readable file is found, else None (the
    run still works; findings are tagged category: unknown).
    """
    env = os.environ.get("MISRA_RULE_TEXTS")
    candidates = [env] if env else []
    if configured:
        candidates.append(os.path.expandvars(configured))

    for cand in candidates:
        if not cand:
            continue
        p = Path(cand)
        if not p.is_absolute():
            p = root / p
        if p.is_file():
            return str(p.resolve())
    return None
