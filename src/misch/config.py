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


def load(config_path: Path, platform_name: str | None = None) -> Config:
    config_path = config_path.resolve()
    if not config_path.is_file():
        raise ConfigError(f"config not found: {config_path}")
    root = config_path.parent

    with open(config_path, "rb") as fh:
        data = tomllib.load(fh)

    if platform_name:
        profiles = data.get("profiles", {})
        if platform_name not in profiles:
            raise ConfigError(f"platform profile not found: {platform_name}")
        _deep_merge(data, profiles[platform_name], profile_name=platform_name)

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


def _deep_merge(target: dict, patch: dict, *, profile_name: str | None = None) -> None:
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

    Raises ``ConfigError`` if ``append_<key>`` references a missing or
    non-list target.
    """
    prefix = f"profile {profile_name!r}: " if profile_name else ""
    for k, v in patch.items():
        if k.startswith("append_"):
            real_key = k[len("append_") :]
            if real_key not in target:
                raise ConfigError(
                    f"{prefix}{k}: key {real_key!r} not found in base config"
                )
            if not isinstance(target[real_key], list):
                raise ConfigError(f"{prefix}{k}: key {real_key!r} is not a list")
            if isinstance(v, list):
                target[real_key].extend(v)
            else:
                target[real_key].append(v)
        elif isinstance(v, dict):
            if k not in target or not isinstance(target[k], dict):
                target[k] = {}
            _deep_merge(target[k], v, profile_name=profile_name)
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
