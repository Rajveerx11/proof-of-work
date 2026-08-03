"""Proof-of-Work — catch AI coding agents cheating; verdict on facts, not opinions."""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("proof-of-work-agent")
except PackageNotFoundError:  # pragma: no cover - source tree without an installed package
    __version__ = "0.0.0+unknown"
