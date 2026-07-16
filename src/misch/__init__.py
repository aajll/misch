"""misch: config-driven MISRA C:2023 analysis for arbitrary C projects."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("misch")
except PackageNotFoundError:
    # package is not installed
    __version__ = "unknown"
