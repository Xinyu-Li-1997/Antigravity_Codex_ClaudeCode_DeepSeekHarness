# Copyright 2026 Xinyu Li (Xinyu-Li-1997)
# SPDX-License-Identifier: Apache-2.0
"""_axiom_lib - cae-axioms 通用公理机器核心内部模块包（纯 Python 3.11+ 标准库）。"""

from __future__ import annotations

_SUBMODULES = (
    "contract",
    "lean_gate",
    "evidence",
    "rules",
    "case",
    "replay",
)

__all__ = ["_SUBMODULES"]
