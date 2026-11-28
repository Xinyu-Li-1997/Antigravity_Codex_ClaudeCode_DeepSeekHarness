#!/usr/bin/env python3
# Copyright 2026 Xinyu Li (Xinyu-Li-1997)
# SPDX-License-Identifier: Apache-2.0
"""axiom_gate.py - 分层硬公理状态机与零依赖纯核 Lean 4 真内核门禁薄 CLI 入口（Python 3.11+ 标准库）。"""

from __future__ import annotations

import argparse
import contextlib
import json
import stat
import sys
from pathlib import Path
from typing import List, Optional

if sys.version_info < (3, 11):
    sys.stderr.write("[!] 错误：axiom_gate.py 需要 Python 3.11 或更高版本。\n")
    raise SystemExit(2)

for _stream_name in ("stdin", "stdout", "stderr"):
    _stream = getattr(sys, _stream_name, None)
    if _stream is not None and hasattr(_stream, "reconfigure"):
        with contextlib.suppress(OSError, ValueError):
            _stream.reconfigure(encoding="utf-8", errors="replace")

_AXIOM_WIN_LINK_REPARSE_TAGS = frozenset({
    getattr(stat, "IO_REPARSE_TAG_SYMLINK", 0xA000000C),
    getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003),
})

_AXIOM_SUBMODULES = (
    "__init__.py",
    "contract.py",
    "lean_gate.py",
    "evidence.py",
    "rules.py",
    "case.py",
    "replay.py",
    "KernelAudit.lean",
)


def _is_unsafe_axiom_link(path: Path) -> bool:
    try:
        st = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if bool(getattr(st, "st_file_attributes", 0) & reparse_flag):
        tag = getattr(st, "st_reparse_tag", 0)
        return tag == 0 or tag in _AXIOM_WIN_LINK_REPARSE_TAGS
    return False


def _verify_axiom_lib_safe(scripts_dir: Path) -> Path:
    if _is_unsafe_axiom_link(scripts_dir) or not scripts_dir.is_dir():
        raise RuntimeError(f"缺少或非法脚本目录: {scripts_dir}")
    pkg_dir = scripts_dir / "_axiom_lib"
    if _is_unsafe_axiom_link(pkg_dir) or not pkg_dir.is_dir():
        raise RuntimeError(f"缺少或非法 _axiom_lib 子包目录: {pkg_dir}")
    for file_name in _AXIOM_SUBMODULES:
        mod_path = pkg_dir / file_name
        if _is_unsafe_axiom_link(mod_path) or not mod_path.is_file():
            raise RuntimeError(f"缺少或非法子模块文件: {mod_path}")
    return pkg_dir


_SCRIPTS_DIR = Path(__file__).resolve().parent
_verify_axiom_lib_safe(_SCRIPTS_DIR)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from _axiom_lib.case import (  # noqa: E402
    cmd_advance,
    cmd_check_lean,
    cmd_feedback,
    cmd_init,
    cmd_report,
    cmd_resolve,
)
from _axiom_lib.contract import (  # noqa: E402
    ALL_STAGES,
    FEEDBACK_LAYERS,
    PROFILES,
    GateError,
)
from _axiom_lib.lean_gate import audit_lean_suite  # noqa: E402
from _axiom_lib.replay import cmd_export_packet, cmd_verify_replay  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="分层硬公理状态机（通用第一层 core + 科学计算 cae）与纯核 Lean 4 真内核反空真门禁")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_lean = sub.add_parser("check-lean", help="对纯核 Lean 4 骨架执行词法预检（失败即短路）及受控真内核 KernelAudit 审计")
    p_lean.add_argument("--profile", default="cae", choices=PROFILES, help="公理档位：core 仅核验 LogicCore.lean；cae 核验全部 4 份骨架（默认 cae）")
    p_lean.add_argument("--lean-dir", default="", help="自定义 .lean 文件目录（默认同技能 lean/）")
    p_lean.add_argument("--run-lean", action="store_true", help="强制执行受控真内核编译、内核重放与全常量公理校验（未安装或失败均硬阻断）")
    p_lean.add_argument("--lean-cmd", default="", help="显式指定本机 lean 可执行文件路径")
    p_lean.add_argument("--wsl-lean", action="store_true", help="在 Windows 端显式经由 WSL2 调用 ~/.elan/bin/lean")

    p_init = sub.add_parser("init", help="初始化公理审查单（--profile core 适用外生目标工程/配置/代码；--profile cae 适用 PDE/力学/科研）")
    p_init.add_argument("--profile", default="cae", choices=PROFILES, help="公理审查档位：core（通用第一层 L0-L3）或 cae（五态 S0-S4，默认 cae）")
    p_init.add_argument("--case", required=True, help="审查单 JSON 输出路径")
    p_init.add_argument("--project-root", required=True, help="待审项目根目录")
    p_init.add_argument("--subject", required=True, help="待审系统、模块、模型或命题名称")
    p_init.add_argument("--claim", default="", help="待审核心宣称（缺省同 --subject）")
    p_init.add_argument("--lean-dir", default="", help="自定义 .lean 文件目录")
    p_init.add_argument("--force", action="store_true", help="允许覆盖已有无冲突历史的初稿审查单")

    p_adv = sub.add_parser("advance", help="按单向序位提交当前关口判定与客观见证")
    p_adv.add_argument("--case", required=True, help="审查单 JSON 路径")
    p_adv.add_argument("--stage", required=True, choices=ALL_STAGES, help="待推进关口（core 为 L0-L3；cae 为 S0-S4）")
    p_adv.add_argument("--payload", required=True, help="包含判定与客观见证的 JSON 文件路径")
    p_adv.add_argument("--project-root", default="", help="可选跨端重定位项目根目录（覆盖审查单原记录路径）")
    p_adv.add_argument("--lean-dir", default="", help="自定义 .lean 文件目录")
    p_adv.add_argument("--run-lean", action="store_true", help="顺带调用本机 lean 真内核核验")
    p_adv.add_argument("--lean-cmd", default="", help="显式指定本机 lean 可执行文件路径")
    p_adv.add_argument("--wsl-lean", action="store_true", help="在 Windows 端显式经由 WSL2 调用 ~/.elan/bin/lean")
    p_adv.add_argument("--allow-probe-exec", action="store_true", help="允许实时运行 payload 中声明的 probe_exec 探针命令")

    p_res = sub.add_parser("resolve", help="在冲突吸收态或 HOLD_L2/HOLD_S3 未定态执行合法出边")
    p_res.add_argument("--case", required=True, help="审查单 JSON 路径")
    p_res.add_argument("--action", required=True, choices=("rebase_ontology", "downgrade_claim", "declare_undetermined"), help="合法出边动作")
    p_res.add_argument("--new-ontology", default="", help="rebase_ontology 时的新构造本体说明")
    p_res.add_argument("--emit-rebased-case", default="", help="rebase_ontology 时可选生成的新审查单路径（严禁与 --case 同路径）")
    p_res.add_argument("--emit-downgraded-case", default="", help="downgrade_claim 时可选生成的新降格审查单路径（严禁与 --case 同路径）")
    p_res.add_argument("--lean-dir", default="", help="建新子单时的自定义 .lean 目录")
    p_res.add_argument("--force", action="store_true", help="允许子单路径覆盖已存在的无冲突历史文件")
    p_res.add_argument("--new-claim", default="", help="downgrade_claim 时的新概念与新命题声明")
    p_res.add_argument("--stripped-claims", action="append", default=[], help="downgrade_claim 时剥离的原宣称项（可多次传）")
    p_res.add_argument("--nonconvergence-tag", default="", help="downgrade_claim 时附加的失收敛或范围收缩标记")
    p_res.add_argument("--rationale", required=True, help="迁移依据说明")

    p_fb = sub.add_parser("feedback", help="登记反例或演进反哺建议（内置防泄漏与防跨层倒灌静态守卫）")
    p_fb.add_argument("--case", required=True, help="审查单 JSON 路径")
    p_fb.add_argument("--target-layer", required=True, choices=sorted(FEEDBACK_LAYERS), help="目标演进层级")
    p_fb.add_argument("--violated-axiom", required=True, help="违反的公理条款或门禁规则编号（如 一.8 / 2M.三.1）")
    p_fb.add_argument("--summary", required=True, help="脱敏抽象最小形式与改进要点简述")
    p_fb.add_argument("--rationale", required=True, help="实战依据")
    p_fb.add_argument("--preconditions", action="append", default=[], help="适用前置条件（可多次传）")
    p_fb.add_argument("--contraindications", action="append", default=[], help="禁适用反指征（可多次传）")
    p_fb.add_argument("--logic-counterexample", default="", help="当 --target-layer 1 时必填的严格逻辑反例依据（有限反模型或 Lean 反例）")
    p_fb.add_argument("--proposed-artifact", default="", help="拟补齐的新定理、探针或条款")

    p_exp = sub.add_parser("export-packet", help="导出纯数据净室盲审复演包（manifest.json + blobs/<sha256>，机械派生全文件与文件树清单）")
    p_exp.add_argument("--case", required=True, help="审查单 JSON 路径")
    p_exp.add_argument("--output-dir", required=True, help="复演包纯净输出目录")
    p_exp.add_argument("--stage", default="", choices=("", *ALL_STAGES), help="可选单关按需导包（省略则导出整单汇聚包）")
    p_exp.add_argument("--project-root", default="", help="可选跨端重定位项目根目录")
    p_exp.add_argument("--include-path", action="append", default=[], help="按 insufficient_context 补充纳入的仓内相对文件路径（可多次传）")
    p_exp.add_argument("--force", action="store_true", help="允许覆写已有的旧复演包目录（仅限含 manifest.json 的包目录）")

    p_ver = sub.add_parser("verify-replay", help="验签 cae_harness --clean-room 净室复演回执并按双正交轴升格 VERIFIED 终态")
    p_ver.add_argument("--case", required=True, help="审查单 JSON 路径")
    p_ver.add_argument("--packet", required=True, help="由 export-packet 生成的复演包目录")
    p_ver.add_argument("--receipt", required=True, help="cae_harness --clean-room 产出的外部净室回执 JSON 文件")
    p_ver.add_argument("--project-root", default="", help="可选跨端重定位项目根目录")
    p_ver.add_argument("--harness-plan-dir", default="", help="cae_harness 状态目录路径（整单升格必填，只读交叉核验票据与 reconcile 账本）")
    p_ver.add_argument("--ticket-id", default="", help="可选显式指定获批的 cae_harness 票据 ID（用于与净室包路径/摘要强绑定核验）")
    p_ver.add_argument("--generator-session-id", default="", help="主会话 ID（整单升格必填，用于阻断同上下文自签）")
    p_ver.add_argument("--generator-model-family", default="", help="主会话模型族标识（用于同源模型族提醒）")
    p_ver.add_argument("--allow-probe-exec", action="store_true", help="允许对本地案卷已锁定的 execute=true 探针终态重跑验签（绝不执行包内命令）")
    p_ver.add_argument("--run-lean", action="store_true", help="若全单含 ast_only 定理见证，现场运行真内核编译校验")
    p_ver.add_argument("--lean-dir", default="", help="自定义 .lean 文件目录")
    p_ver.add_argument("--lean-cmd", default="", help="显式指定本机 lean 可执行文件路径")
    p_ver.add_argument("--wsl-lean", action="store_true", help="在 Windows 端显式经由 WSL2 调用 ~/.elan/bin/lean")

    p_rep = sub.add_parser("report", help="生成结构化与 Markdown 公理审查报告（现场重算全链双哈希与复演新鲜度）")
    p_rep.add_argument("--case", required=True, help="审查单 JSON 路径")
    p_rep.add_argument("--project-root", default="", help="可选跨端重定位项目根目录（覆盖审查单原记录路径）")
    p_rep.add_argument("--output-md", default="", help="可选 Markdown 审计报告输出路径")
    p_rep.add_argument("--lean-dir", default="", help="自定义 .lean 文件目录")
    p_rep.add_argument("--run-lean", action="store_true", help="报告中同步调用本机 lean 真内核编译状态")
    p_rep.add_argument("--lean-cmd", default="", help="显式指定本机 lean 可执行文件路径")
    p_rep.add_argument("--wsl-lean", action="store_true", help="在 Windows 端显式经由 WSL2 调用 ~/.elan/bin/lean")
    p_rep.add_argument("--require-verified", action="store_true", help="强制要求审查单处于未失活的 VERIFIED_NO_T 或 VERIFIED_T_ASSERTED，否则返回退出码 2")
    p_rep.add_argument("--require-t-asserted", action="store_true", help="强制要求审查单处于未失活的 VERIFIED_T_ASSERTED（PASSED_ALL_GATES），否则返回退出码 2")
    p_rep.add_argument("--allow-probe-exec", action="store_true", help="配合严格验收门禁在终态哈希下现场重跑本地已锁定探针")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    dispatch = {
        "check-lean": cmd_check_lean,
        "init": cmd_init,
        "advance": cmd_advance,
        "resolve": cmd_resolve,
        "feedback": cmd_feedback,
        "export-packet": cmd_export_packet,
        "verify-replay": cmd_verify_replay,
        "report": cmd_report,
    }
    try:
        return dispatch[args.cmd](args)
    except GateError as exc:
        print(json.dumps({
            "ok": False,
            "error_code": exc.code,
            "error": exc.message,
            **({"details": exc.details} if exc.details else {}),
        }, ensure_ascii=False, indent=2))
        return 2


__all__ = [
    "ALL_STAGES",
    "FEEDBACK_LAYERS",
    "PROFILES",
    "GateError",
    "audit_lean_suite",
    "build_parser",
    "cmd_advance",
    "cmd_check_lean",
    "cmd_feedback",
    "cmd_init",
    "cmd_report",
    "cmd_resolve",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
