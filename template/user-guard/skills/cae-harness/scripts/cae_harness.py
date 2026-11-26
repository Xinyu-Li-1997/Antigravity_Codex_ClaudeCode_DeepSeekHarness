#!/usr/bin/env python3


"""Antigravity、Codex、Claude Code 与 DeepSeek Harness 跨端任务派发与验收工具。"""


import argparse
import ast
import base64
import collections
import concurrent.futures
import contextlib
import copy
import datetime
import fnmatch
import hashlib
import ipaddress
import itertools
import json
import math
import ntpath
import os
import posixpath
import queue
import re
import shutil
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import uuid
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener, urlopen

_STDLIB_URLOPEN = urlopen



# ════════════════════════════════════════════════════════════════════════════
# 内部子包装配入口：先校验目录与子模块均非符号链接/重解析点，再按固定顺序载入命名空间
# ════════════════════════════════════════════════════════════════════════════

_HARNESS_SUBMODULES = (
    'config',
    'catalog',
    'engines',
    'grounding',
    'reconcile',
    'read_plan',
    'dispatch',
)

_HARNESS_WIN_LINK_REPARSE_TAGS = frozenset({
    getattr(stat, 'IO_REPARSE_TAG_SYMLINK', 0xA000000C),
    getattr(stat, 'IO_REPARSE_TAG_MOUNT_POINT', 0xA0000003),
})


def _is_unsafe_harness_link(path: Path) -> bool:
    try:
        st = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    reparse_flag = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)
    if bool(getattr(st, 'st_file_attributes', 0) & reparse_flag):
        tag = getattr(st, 'st_reparse_tag', 0)
        return tag == 0 or tag in _HARNESS_WIN_LINK_REPARSE_TAGS
    return False


def _load_harness_submodules(namespace: dict, scripts_dir: Path) -> None:
    if _is_unsafe_harness_link(scripts_dir) or not scripts_dir.is_dir():
        raise RuntimeError(f'缺少或非法脚本目录: {scripts_dir}')
    pkg_dir = scripts_dir / '_harness'
    if _is_unsafe_harness_link(pkg_dir) or not pkg_dir.is_dir():
        raise RuntimeError(f'缺少或非法 _harness 子包目录: {pkg_dir}')
    for mod_name in _HARNESS_SUBMODULES:
        mod_path = pkg_dir / f'{mod_name}.py'
        if _is_unsafe_harness_link(mod_path) or not mod_path.is_file():
            raise RuntimeError(f'缺少或非法子模块文件: {mod_path}')
        source = mod_path.read_text(encoding='utf-8')
        code = compile(source, str(mod_path), 'exec')
        exec(code, namespace)


_HARNESS_SCRIPTS_DIR = Path(__file__).resolve().parent
_load_harness_submodules(globals(), _HARNESS_SCRIPTS_DIR)


if __name__ == '__main__':
    raise SystemExit(main())
