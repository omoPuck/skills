#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run-python skill：从 Python 源码提取第三方顶层导入名（仅标准库，ast 解析）。

输出 JSON：{"imports": [...], "dist_names": [...]}
  imports     —— 代码里 import 的名字（探测环境用）
  dist_names  —— 对应的 pip 发行包名（安装用；常见映射见 IMPORT_TO_DIST）

用法：
  analyze_imports.py --file <path.py>
  analyze_imports.py --code "<代码>"
  都不给时读 stdin
"""

import argparse
import ast
import json
import sys

IMPORT_TO_DIST = {
    "PIL": "Pillow",
    "bs4": "beautifulsoup4",
    "yaml": "PyYAML",
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "skimage": "scikit-image",
    "dotenv": "python-dotenv",
    "dateutil": "python-dateutil",
    "zmq": "pyzmq",
    "nacl": "PyNaCl",
    "Crypto": "pycryptodome",
    "jwt": "PyJWT",
    "flask_cors": "Flask-Cors",
}


def _stdlib():
    try:
        return set(sys.stdlib_module_names)
    except AttributeError:  # Python < 3.10
        return {
            "abc", "argparse", "ast", "asyncio", "base64", "bisect", "builtins",
            "cmath", "collections", "concurrent", "configparser", "contextlib",
            "copy", "csv", "ctypes", "dataclasses", "datetime", "decimal",
            "difflib", "dis", "enum", "errno", "functools", "gc", "getpass",
            "glob", "gzip", "hashlib", "heapq", "hmac", "html", "http",
            "importlib", "inspect", "io", "itertools", "json", "keyword",
            "linecache", "locale", "logging", "lzma", "math", "multiprocessing",
            "os", "pathlib", "pickle", "platform", "queue", "random", "re",
            "secrets", "select", "shlex", "shutil", "signal", "site", "socket",
            "sqlite3", "ssl", "stat", "statistics", "string", "struct",
            "subprocess", "sys", "tempfile", "textwrap", "threading", "time",
            "timeit", "token", "tokenize", "traceback", "types", "typing",
            "unicodedata", "unittest", "urllib", "uuid", "venv", "warnings",
            "weakref", "webbrowser", "xml", "zipfile", "zoneinfo",
        }


STDLIB = _stdlib()


def extract(code):
    tree = ast.parse(code)
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0].strip()
                if root and root not in STDLIB:
                    out.add(root)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue  # 相对导入忽略
            if node.module:
                root = node.module.split(".")[0].strip()
                if root and root not in STDLIB:
                    out.add(root)
    return sorted(out)


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="从 Python 源码提取第三方顶层导入名")
    p.add_argument("--file", help=".py 文件路径")
    p.add_argument("--code", help="内联代码字符串")
    args = p.parse_args(argv)

    if args.file:
        try:
            with open(args.file, encoding="utf-8", errors="replace") as fh:
                code = fh.read()
        except OSError as exc:
            print("cannot read %s: %s" % (args.file, exc), file=sys.stderr)
            return 2
    elif args.code:
        code = args.code
    else:
        code = sys.stdin.read()

    try:
        imports = extract(code)
    except SyntaxError as exc:
        print("syntax error: %s" % exc, file=sys.stderr)
        return 2

    dist = []
    seen = set()
    for name in imports:
        d = IMPORT_TO_DIST.get(name, name)
        if d in seen:
            continue
        seen.add(d)
        dist.append(d)

    json.dump({"imports": imports, "dist_names": dist}, sys.stdout, ensure_ascii=False)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
