# Copyright 2026 Xinyu Li (Xinyu-Li-1997)
# SPDX-License-Identifier: Apache-2.0
"""_harness - cae_harness.py 内部模块化子包元数据（仅依赖 Python 3.11+ 标准库）。

安全加载与符号链接校验由入口脚本 cae_harness.py 在任何导入执行前直接完成。
"""

SUBMODULES = (
    'config',
    'catalog',
    'engines',
    'grounding',
    'reconcile',
    'read_plan',
    'dispatch',
)
