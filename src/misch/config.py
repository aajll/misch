"""Load and validate `misra.toml`.

Everything that differs between projects is data here, not shell. See
docs/configuration.md for the full schema.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .config_schema import (
    RUNTIME_DEFAULTS,
    VALID_DB_SOURCES,
    profile_allows_append,
    profile_field,
    profile_section_supported,
    profile_value_description,
    strip_append,
    valid_profile_value,
)


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
            available = ", ".join(sorted(profiles)) or "none"
            raise ConfigError(
                f"profile not found: {profile_name!r} (available: {available})"
            )
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

    db_source = db.get("source", RUNTIME_DEFAULTS.db_source)
    if db_source not in VALID_DB_SOURCES:
        raise ConfigError(
            f"db.source must be one of {sorted(VALID_DB_SOURCES)}, got {db_source!r}"
        )

    # Handle platform as a dict or a string for backward compatibility.
    # preset and xml are mutually exclusive selectors; reject a config that
    # sets both rather than silently preferring one.
    if isinstance(platform, dict):
        if "xml" in platform and "preset" in platform:
            raise ConfigError("platform: set either 'preset' or 'xml', not both")
        plat = (
            str(_configured_path(platform["xml"], root, "platform.xml"))
            if "xml" in platform
            else platform.get("preset") or RUNTIME_DEFAULTS.platform
        )
    else:
        plat = str(platform)

    outputs = report.get("outputs") or [{"format": "terminal"}]
    outputs = [_norm_output(o, root) for o in outputs]

    return Config(
        project_root=root,
        scope=list(project.get("scope", RUNTIME_DEFAULTS.scope)),
        exclude=list(project.get("exclude", RUNTIME_DEFAULTS.exclude)),
        db_source=db_source,
        db_path=db.get("path"),
        platform=plat,
        defines=list(toolchain.get("defines", RUNTIME_DEFAULTS.defines)),
        rule_texts=_resolve_rule_texts(rules.get("texts"), root),
        outputs=outputs,
        baseline_path=_resolve_path(baseline.get("path"), root, "misra-baseline.json"),
        suppressions_path=_optional_path(deviations.get("suppressions"), root),
    )


def _validate_profile(profile: dict, profile_name: str) -> None:
    """Reject profile keys and values outside the supported overlay schema."""
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
                _profile_error(profile_name, path, f"must be {expected}")



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

        [profiles.aarch64]
        platform.xml = "analysis/aarch64_platform.xml"
        toolchain.append_defines = ["ARCH_ARM64"]
        project.append_exclude = ["generated/"]

    Scalar values are replaced normally and ``toolchain.defines`` /
    ``project.exclude`` are appended to.  The top-level ``platform`` table is
    replaced wholesale rather than deep-merged, because ``preset`` and ``xml``
    are mutually exclusive: a profile's platform fully supersedes the base's.

    ``append_<key>`` is permitted only for documented list settings. It raises
    ``ConfigError`` for a non-list or unsupported target. If a supported target
    is missing from the base, it is auto-initialised as an empty list.
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
