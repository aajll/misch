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
    ProfileError,
    merge,
    validate_profile,
)


class ConfigError(Exception):
    """Raised when configuration is invalid or missing."""


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
        try:
            validate_profile(profile, profile_name)
        except ProfileError as e:
            raise ConfigError(str(e)) from e
        try:
            merge(data, profile, profile_name=profile_name)
        except ProfileError as e:
            raise ConfigError(str(e)) from e

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
