"""Learned, versioned cheat-pattern rules — grown by the self-improving loop under a hard
safety gate. Ships empty (no cheats learned in the wild yet); `proof-of-work learn` extends it.
"""
from __future__ import annotations

import os

RULES_FILE = os.path.join(os.path.dirname(__file__), "learned.json")
