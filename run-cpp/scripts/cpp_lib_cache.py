#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""run-cpp skill：D:\tools\cpp-libs 外部库本地缓存管理器（仅标准库，不联网）。

只操作缓存 JSON 文件。库安装在 D:\tools\cpp-libs 下，结构约定见 SKILL.md：
  <lib>/<toolchain>-<arch>/<version>/include|lib
缓存记录每个库的 toolchain/arch/version/include 路径/库文件/安装命令，
供后续任务直接复用（构造 -I 与 -L 参数），不重复下载。

用法（用任意 python 跑）：
  cpp_lib_cache.py list
  cpp_lib_cache.py get <lib>
  cpp_lib_cache.py have <lib> --toolchain <gcc|msvc> --arch <x64|arm64>
  cpp_lib_cache.py add <lib> --toolchain <gcc|msvc> --arch <x64|arm64> --version <v> --include <绝对路径> [--lib <绝对路径>] [--install-command "..."]
  cpp_lib_cache.py sync <lib> --toolchain <gcc|msvc> --arch <x64|arm64>
  cpp_lib_cache.py require <lib> --toolchain <gcc|msvc> --arch <x64|arm64>   # 若已在本机可用，自动登记；缺→stderr+exit1
  cpp_lib_cache.py gen-flags <libs...> --toolchain <gcc|msvc> --arch <x64|arm64>   # 输出 -I/-L 参数(空格分隔)
  cpp_lib_cache.py active [<lib>] [--session <id>] [--toolchain <gcc|msvc>] [--arch <x64|arm64>]  # 会话活动库集

所有命令可用 --cache <path> 覆盖缓存文件路径（默认本 skill 目录 cpp-libs-cache.json）。
"""

import argparse
import datetime as _dt
import json
import os
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CACHE = SKILL_DIR / "cpp-libs-cache.json"
FALLBACK_CACHE = Path.home() / ".cpp-libs-cache.json"
CACHE_VERSION = 1
LIBS_ROOT = Path(r"D:\tools\cpp-libs")

# 国内镜像（预编译包优先走镜像，失败再走官方源）
MIRRORS = {
    "ghproxy": "https://ghproxy.com/https://github.com/",
    "gh-proxy.com": "https://gh-proxy.com/",
    "mirror.ghproxy.cn": "https://mirror.ghproxy.cn/",
    "github": "https://github.com/",
}
# 常见"镜像下载包"的国内源
CN_MIRRORS = [
    "https://mirrors.tuna.tsinghua.edu.cn/",
    "https://mirrors.aliyun.com/",
    "https://mirrors.ustc.edu.cn/",
]

VALID_TOOLCHAINS = ("gcc", "msvc")
VALID_ARCHS = ("x64", "arm64", "x86")


def now_iso():
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def default_data():
    return {
        "version": CACHE_VERSION,
        "updatedAt": now_iso(),
        "defaultToolchain": "gcc",
        "libsRoot": str(LIBS_ROOT),
        "mirrors": {"github": MIRRORS["github"], "prefer": list(MIRRORS)},
        "libraries": {},   # lib -> { toolchain -> arch -> {...} }
        "session": {},     # sid -> {"libs":[...], "toolchain":..., "arch":...}
    }


def _setdefaults(data):
    data["version"] = CACHE_VERSION
    data.setdefault("updatedAt", now_iso())
    data.setdefault("libsRoot", str(LIBS_ROOT))
    data.setdefault("mirrors", {"github": MIRRORS["github"], "prefer": list(MIRRORS)})
    data.setdefault("libraries", {})
    data.setdefault("session", {})
    if not isinstance(data.get("libraries"), dict):
        data["libraries"] = {}
    if not isinstance(data.get("session"), dict):
        data["session"] = {}
    return data


def load_cache(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = default_data()
    if not isinstance(data, dict):
        data = default_data()
    return _setdefaults(data)


def save_cache(path, data):
    data["updatedAt"] = now_iso()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_cache(override):
    if override:
        p = Path(override).expanduser().resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    try:
        SKILL_DIR.mkdir(parents=True, exist_ok=True)
        with open(DEFAULT_CACHE, "a", encoding="utf-8"):
            pass
        return DEFAULT_CACHE
    except Exception:
        return FALLBACK_CACHE


# ---------- 本机探测 ----------

def detect_arch():
    a = os.environ.get("PROCESSOR_ARCHITECTURE", "").lower()
    if a in ("arm64", "aarch64"):
        return "arm64"
    if a in ("x86", "ia32"):
        return "x86"
    return "x64"


def detect_toolchain():
    # 探测常见 mingw / cl
    candidates = [
        ("gcc", r"D:\tools\QT\Tools\mingw1310_64\bin\g++.exe"),
        ("gcc", r"D:\tools\mingw64\bin\g++.exe"),
        ("gcc", r"C:\msys64\mingw64\bin\g++.exe"),
        ("gcc", r"C:\mingw64\bin\g++.exe"),
        ("gcc", r"C:\Program Files\mingw-w64\x86_64-*_gcc*\bin\g++.exe"),
        ("msvc", r"C:\Program Files\Microsoft Visual Studio\*\Community\VC\Tools\MSVC\*\bin\Hostx64\x64\cl.exe"),
        ("msvc", r"C:\Program Files (x86)\Microsoft Visual Studio\*\BuildTools\VC\Tools\MSVC\*\bin\Hostx64\x64\cl.exe"),
    ]
    import glob
    for tc, pat in candidates:
        if "*" in pat:
            for hit in glob.glob(pat):
                if os.path.exists(hit):
                    return tc
        elif os.path.exists(pat):
            return tc
    # PATH 兜底
    for tc, exe in (("msvc", "cl.exe"), ("gcc", "g++.exe")):
        for d in os.environ.get("PATH", "").split(os.pathsep):
            if d and os.path.exists(os.path.join(d, exe)):
                return tc
    return "gcc"


# ---------- 子命令 ----------

def cmd_list(args, cache, data):
    libs = data["libraries"]
    if not libs:
        print("(empty cache) %s" % cache)
        return 0
    print("# cache: %s" % cache)
    for lib in sorted(libs):
        for tc, archs in libs[lib].items():
            for arch, e in (archs.items() if isinstance(archs, dict) else []):
                VER = e.get("version", "?") if isinstance(e, dict) else "?"
                print("%-16s %-5s %-5s v%-10s %s" % (lib, tc, arch, VER, e.get("include", "") if isinstance(e, dict) else ""))
    return 0


def cmd_get(args, cache, data):
    lib = data["libraries"].get(args.lib)
    if lib is None:
        print("no such lib in cache: %s" % args.lib, file=sys.stderr)
        return 1
    print(json.dumps(lib, ensure_ascii=False, indent=2))
    return 0


def _variant(libs, lib, tc, arch):
    v = libs.get(lib)
    if v is None:
        return 0
    va = v.get(tc, {})
    if not isinstance(va, dict):
        return 0
    return 1 if arch in va else 0


def cmd_have(args, cache, data):
    if _variant(data["libraries"], args.lib, args.toolchain, args.arch):
        v = data["libraries"][args.lib].get(args.toolchain, {}).get(args.arch, {})
        print(json.dumps(v, ensure_ascii=False, indent=2))
        return 0
    print("NOT_INSTALLED", file=sys.stderr)
    return 1


def cmd_add(args, cache, data):
    e = data["libraries"].setdefault(args.lib, {}).setdefault(args.toolchain, {}).setdefault(args.arch, {})
    if args.version:
        e["version"] = args.version
    if args.include:
        e["include"] = args.include
    if args.lib_path:
        e["lib"] = args.lib_path
    if args.install_command:
        e["installCommand"] = args.install_command
    e["updatedAt"] = now_iso()
    save_cache(cache, data)
    print("added %s (%s/%s): %s" % (args.lib, args.toolchain, args.arch, cache))
    return 0


def cmd_sync(args, cache, data):
    r"""重扫本机 D:\tools\cpp-libs 下该库目录，登记 include/lib 是否存在。"""
    libdir = LIBS_ROOT / args.lib
    if not libdir.exists():
        print("NOT_FOUND dir %s" % libdir, file=sys.stderr)
        return 1
    inc = None
    lib = None
    for p in sorted(libdir.rglob("include")):
        if p.is_dir():
            inc = str(p)
            break
    cands = []
    for ex in ("*.lib", "*.a"):
        cands += [str(x) for x in libdir.rglob(ex)]
    if cands:
        lib = list(cands)
    e = data["libraries"].setdefault(args.lib, {}).setdefault(args.toolchain, {}).setdefault(args.arch, {})
    if inc:
        e["include"] = inc
    if lib:
        e["lib"] = lib
    e["updatedAt"] = now_iso()
    save_cache(cache, data)
    print("synced %s (%s/%s) include=%s" % (args.lib, args.toolchain, args.arch, inc))
    return 0


def cmd_require(args, cache, data):
    """库应在本机 cpp-libs 下已有或已登记；没有则提示需下载。返回 0 表示满足。"""
    if _variant(data["libraries"], args.lib, args.toolchain, args.arch):
        return 0
    # 可能在磁盘上但没登记 → 尝试登记
    libdir = LIBS_ROOT / args.lib
    if libdir.exists():
        sub = argparse.Namespace(lib=args.lib, toolchain=args.toolchain, arch=args.arch)
        rc = cmd_sync(sub, cache, data)
        if rc == 0 and _variant(data["libraries"], args.lib, args.toolchain, args.arch):
            return 0
    print("require %s (%s/%s): NOT available in %s" % (args.lib, args.toolchain, args.arch, LIBS_ROOT), file=sys.stderr)
    return 1


def cmd_now_flags(args, cache, data):
    parts = []
    missing = []
    for lib in args.libs:
        v = data["libraries"].get(lib, {}).get(args.toolchain, {}).get(args.arch)
        if not v:
            missing.append(lib)
            continue
        if v.get("include"):
            parts += ["-I%s" % v["include"]]
        libdirs = v.get("lib")
        if libdirs:
            if isinstance(libdirs, str):
                libdirs = [libdirs]
            for l in libdirs:
                parts.append("-L%s" % l)
    if missing:
        print("MISSING_LIBS: %s" % ",".join(missing), file=sys.stderr)
        return 1
    print(" ".join(parts))
    return 0


def cmd_active(args, cache, data):
    sid = args.session
    s = data["session"].setdefault(sid, {})
    if args.lib:
        # set: append lib if not present
        libs = list(s.get("libs", []))
        if args.lib not in libs:
            libs.append(args.lib)
        s["libs"] = libs
        s["toolchain"] = args.toolchain
        s["arch"] = args.arch
        save_cache(cache, data)
        print("active %s: %s" % (sid, ",".join(s["libs"])))
        return 0
    # get
    libs = s.get("libs", [])
    if not libs:
        print("NONE")
        return 1
    print(json.dumps(s, ensure_ascii=False))
    return 0


# ---------- 入口 ----------

def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="run-cpp 外部库缓存管理器（仅标准库）")
    p.add_argument("--cache", help="覆盖缓存文件路径")
    sub = p.add_subparsers(dest="cmd", required=True)

    def _tc_args(sp):
        sp.add_argument("--toolchain", choices=VALID_TOOLCHAINS, default=None, help="gcc|msvc")
        sp.add_argument("--arch", choices=VALID_ARCHS, default=None, help="x64|arm64|x86")
        return sp

    sub.add_parser("list", help="列出缓存中的库")

    g = sub.add_parser("get", help="输出某个库的完整缓存")
    g.add_argument("lib")

    g = _tc_args(sub.add_parser("have", help="检查库是否已装（toolchain/arch）"))
    g.add_argument("lib")

    g = _tc_args(sub.add_parser("add", help="登记一个已安装的库"))
    g.add_argument("lib")
    g.add_argument("--version")
    g.add_argument("--include")
    g.add_argument("--lib-path")
    g.add_argument("--install-command")

    g = _tc_args(sub.add_parser("sync", help="扫描 D:\\tools\\cpp-libs\\<lib> 登记 include/lib"))
    g.add_argument("lib")

    g = _tc_args(sub.add_parser("require", help="要求库存在，缺则报 MISSING"))
    g.add_argument("lib")

    g = _tc_args(sub.add_parser("now-flags", help="输出 -I/-L 编译参数(空格分隔)"))
    g.add_argument("libs", nargs="+")

    g = _tc_args(sub.add_parser("active", help="查看/设置会话活动库集"))
    g.add_argument("lib", nargs="?")
    g.add_argument("--session", default="default")

    args = p.parse_args(argv)
    cache = resolve_cache(args.cache)
    data = load_cache(cache)

    # 未显式给定则自动探测本机（list/get 等子命令可能没有这两个属性）
    if getattr(args, "toolchain", None) is None:
        args.toolchain = detect_toolchain()
    if getattr(args, "arch", None) is None:
        args.arch = detect_arch()

    handlers = {
        "list": cmd_list,
        "get": cmd_get,
        "have": cmd_have,
        "add": cmd_add,
        "sync": cmd_sync,
        "require": cmd_require,
        "now-flags": cmd_now_flags,
        "active": cmd_active,
    }
    return handlers[args.cmd](args, cache, data)


if __name__ == "__main__":
    sys.exit(main())