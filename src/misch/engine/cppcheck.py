"""Drive cppcheck + the misra.py addon and normalise its XML to Findings.

cppcheck is always run in XML mode; we never scrape human text. That keeps the
Finding model the single source of truth for every downstream projection.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from ..config import Config
from ..report.headlines import RuleInfo
from ..report.model import Category, Finding, Source, is_misra_id

# cppcheck's own checks that add noise without bearing on MISRA scope. Kept
# minimal and overridable; the deviation machinery manages the rest.
_DEFAULT_SUPPRESSIONS = [
    "missingIncludeSystem",
    "unusedFunction",
    "checkersReport",
    "normalCheckLevelMaxBranches",
]


class EngineError(Exception):
    pass


def run(
    cfg: Config,
    db_path: Path,
    rules: dict[str, RuleInfo],
    *,
    inline_suppr: bool = True,
) -> list[Finding]:
    """Run cppcheck and normalise its XML to Findings.

    With ``inline_suppr=False`` the ``cppcheck-suppress`` comments *and* the
    project suppressions file are ignored, so suppressed findings reappear. The
    deviation staleness check uses this to see what each suppression is actually
    hiding.
    """
    if shutil.which("cppcheck") is None:
        raise EngineError("cppcheck not found on PATH")

    with tempfile.TemporaryDirectory() as td:
        addon = Path(td) / "misra.json"
        args = ["--rule-texts=" + cfg.rule_texts] if cfg.rule_texts else []
        addon.write_text(json.dumps({"script": "misra.py", "args": args}))

        cmd = _build_cmd(cfg, db_path, addon, inline_suppr=inline_suppr)
        proc = subprocess.run(cmd, capture_output=True, text=True)
        # cppcheck writes results as XML to stderr. A non-zero exit with no XML
        # is a real failure (bad flags, missing addon, ...).
        if not proc.stderr.strip().startswith("<?xml") and proc.returncode != 0:
            raise EngineError(
                f"cppcheck failed (exit {proc.returncode}):\n{proc.stderr[:2000]}"
            )
        return _parse_xml(proc.stderr, cfg.project_root, rules)


def _build_cmd(
    cfg: Config, db_path: Path, addon: Path, *, inline_suppr: bool
) -> list[str]:
    """Assemble the cppcheck invocation.

    Inline ``cppcheck-suppress`` comments and the project suppressions file are
    applied together and only when ``inline_suppr`` is set, so the staleness
    check (``inline_suppr=False``) sees every finding a suppression hides.
    """
    cmd = [
        "cppcheck",
        f"--project={db_path}",
        f"--addon={addon}",
        f"--platform={cfg.platform}",
        "--enable=all",
        "--xml",
        "--xml-version=2",
    ]
    if inline_suppr:
        cmd.append("--inline-suppr")
        if cfg.suppressions_path and cfg.suppressions_path.is_file():
            cmd.append(f"--suppressions-list={cfg.suppressions_path}")
    for sup in _DEFAULT_SUPPRESSIONS:
        cmd.append(f"--suppress={sup}")
    for d in cfg.defines:
        cmd.append(f"-D{d}")
    # Engine-level skip for directory excludes (post-filter is authoritative).
    for ex in cfg.exclude:
        base = ex.rstrip("/")
        if "*" not in base and "?" not in base:
            cmd.append("-i")
            cmd.append(str(cfg.project_root / base))
    return cmd


def _parse_xml(
    xml_text: str,
    root: Path,
    rules: dict[str, RuleInfo],
) -> list[Finding]:
    try:
        tree = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise EngineError(f"could not parse cppcheck XML: {exc}") from None

    findings: list[Finding] = []
    for err in tree.iter("error"):
        rule_id = err.get("id", "")
        loc = err.find("location")
        if loc is None:
            continue  # non-located info (toolchain notes, etc.)
        file_rel = _rel(root, loc.get("file", ""))
        misra = is_misra_id(rule_id)
        info = rules.get(rule_id.lower())
        # cppcheck emits the symbol name as a <symbol> child element (and, on
        # the <location>, sometimes an `info` attribute); prefer the child.
        sym_el = err.find("symbol")
        symbol = sym_el.text if sym_el is not None and sym_el.text else ""
        findings.append(
            Finding(
                rule_id=rule_id,
                message=err.get("msg", ""),
                file=file_rel,
                line=int(loc.get("line", "0") or 0),
                column=int(loc.get("column", "0") or 0),
                severity=err.get("severity", "style"),
                category=info.category if info else Category.UNKNOWN,
                headline=info.headline if info else "",
                source=Source.MISRA if misra else Source.CPPCHECK,
                symbol=symbol,
            )
        )
    return findings


def _rel(root: Path, file: str) -> str:
    if not file:
        return ""
    p = Path(file)
    try:
        return p.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return p.as_posix()
