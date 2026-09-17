#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run-python skill：conda 环境本地缓存管理器（仅标准库）。

只操作缓存 JSON 文件：不启动子进程、不做网络访问。
缓存默认位于本 skill 目录下的 env-cache.json，不可写时回退到
~/.run-python-cache.json（执行变更命令时会打印实际使用的路径）。

用法（用 conda 根环境的 python 运行）：
  env_cache.py list
  env_cache.py get <env>
  env_cache.py match --requires pkgA pkgB ...
  env_cache.py name-for --requires pkgA pkgB ...
  env_cache.py add <env> --python 3.12.x --path <env目录> [--created-for "..."] [--install-command "..."] [--from-file freeze.txt]
  env_cache.py sync <env> --from-file freeze.txt
  env_cache.py maybe-sync <env> --from-file freeze.txt --chance 0.1
  env_cache.py touch <env>
  env_cache.py active                    # 查询本会话窗口的活动环境（无则输出 NONE）
  env_cache.py active <env> [--session <窗口id>]   # 记录本会话窗口的活动环境

所有命令可用 --cache <path> 覆盖缓存文件路径。
"""

import argparse
import datetime as _dt
import hashlib
import json
import random
import re
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CACHE = SKILL_DIR / "env-cache.json"
FALLBACK_CACHE = Path.home() / ".run-python-cache.json"
CACHE_VERSION = 1

PIP_MIRRORS = [
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "https://mirrors.aliyun.com/pypi/simple/",
    "https://mirrors.ustc.edu.cn/pypi/simple/",
]

# 常见 import 名 → pip 发行包名（探测环境用 import 名，安装/缓存用发行名）
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


def _utf8_stdio():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def now_iso():
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def normalize_dist(name):
    """发行包名归一化：去版本/约束/extras，小写，下划线转连字符。"""
    name = name.strip().lstrip("\ufeff")  # 去掉 Windows 写文件可能带上的 BOM
    name = re.split(r"[=<>!~;\[@]", name, maxsplit=1)[0].strip()
    return name.lower().replace("_", "-")


def dist_for(import_name):
    return IMPORT_TO_DIST.get(import_name, import_name)


def split_name_version(line):
    line = line.strip()
    if not line or line.startswith("#") or line.startswith("-e"):
        return None
    for sep in ("==", ">=", "<=", "~=", "!=", "==="):
        if sep in line:
            name, ver = line.split(sep, 1)
            return name.strip(), ver.strip()
    if "@" in line:
        name, _rest = line.split("@", 1)
        return name.strip(), None
    toks = line.split()
    return (toks[0], None) if toks else None


def parse_freeze(text):
    out = []
    for line in text.splitlines():
        pair = split_name_version(line)
        if pair is None:
            continue
        name, ver = pair
        if not name:
            continue
        out.append(name + (("==" + ver) if ver else ""))
    return out


def default_cache_data():
    return {
        "version": CACHE_VERSION,
        "updatedAt": now_iso(),
        "mirror": {"pip": PIP_MIRRORS[0], "fallbacks": PIP_MIRRORS[1:]},
        "session": {},
        "environments": {},
    }


def load_cache(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default_cache_data()
    if not isinstance(data, dict):
        return default_cache_data()
    data["version"] = CACHE_VERSION
    data.setdefault("updatedAt", now_iso())
    data.setdefault("mirror", {"pip": PIP_MIRRORS[0], "fallbacks": PIP_MIRRORS[1:]})
    data.setdefault("environments", {})
    if not isinstance(data["environments"], dict):
        data["environments"] = {}
    data.setdefault("session", {})
    if not isinstance(data["session"], dict):
        data["session"] = {}
    return data


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


def entry_summary(name, e):
    return "%-22s python=%-10s pkgs=%-4d used=%-10s note=%s" % (
        name,
        e.get("python", "?"),
        len(e.get("packages", [])),
        (e.get("lastUsedAt") or "-")[:10],
        e.get("createdFor", ""),
    )


def packages_from_text(text):
    return parse_freeze(text)


def diff_packages(old, new):
    o = {normalize_dist(p.split("==", 1)[0]): p for p in old}
    n = {normalize_dist(p.split("==", 1)[0]): p for p in new}
    added = sorted(set(n) - set(o))
    removed = sorted(set(o) - set(n))
    changed = sorted(k for k in set(o) & set(n) if o[k] != n[k])
    return added, removed, changed


def read_freeze_file(path):
    # utf-8-sig 兼容 Windows PowerShell 5.1 的 Out-File 写入的带 BOM 文件
    return Path(path).read_text(encoding="utf-8-sig")


# ---------- 子命令 ----------

def cmd_list(args, cache, data):
    envs = data["environments"]
    if not envs:
        print("(empty cache) %s" % cache)
        return 0
    print("# cache: %s" % cache)
    for name in sorted(envs):
        print(entry_summary(name, envs[name]))
    return 0


def cmd_get(args, cache, data):
    e = data["environments"].get(args.env)
    if e is None:
        print("no such env in cache: %s" % args.env, file=sys.stderr)
        return 1
    print(json.dumps(e, ensure_ascii=False, indent=2))
    return 0


def cmd_match(args, cache, data):
    required = {normalize_dist(dist_for(r)) for r in args.requires}
    hits = []
    for name, e in data["environments"].items():
        installed = {normalize_dist(p.split("==", 1)[0]) for p in e.get("packages", [])}
        if required <= installed:
            hits.append(name)
    for name in hits:
        print(name)
    return 0


def cmd_name_for(args, cache, data):
    names = sorted({normalize_dist(dist_for(r)) for r in args.requires})
    digest = hashlib.sha1("|".join(names).encode("utf-8")).hexdigest()[:8]
    print("runpy-" + digest)
    return 0


def cmd_add(args, cache, data):
    envs = data["environments"]
    e = envs.get(args.env)
    if e is None:
        e = {"createdAt": now_iso()}
    if args.python:
        e["python"] = args.python
    if args.path:
        e["path"] = args.path
    if args.created_for:
        e["createdFor"] = args.created_for
    if args.install_command:
        e["installCommand"] = args.install_command
    if args.from_file:
        e["packages"] = packages_from_text(read_freeze_file(args.from_file))
    e["lastSyncedAt"] = now_iso()
    envs[args.env] = e
    save_cache(cache, data)
    print("cache updated: %s (%d packages)" % (cache, len(e.get("packages", []))))
    return 0


def cmd_sync(args, cache, data):
    envs = data["environments"]
    new_pkgs = packages_from_text(read_freeze_file(args.from_file))
    if args.env not in envs:
        e = {"createdAt": now_iso(), "createdFor": "auto-sync (new)"}
        envs[args.env] = e
    else:
        e = envs[args.env]
    old_pkgs = e.get("packages", [])
    added, removed, changed = diff_packages(old_pkgs, new_pkgs)
    if not added and not removed and not changed:
        print("no change")
        return 0
    e["packages"] = new_pkgs
    e["lastSyncedAt"] = now_iso()
    save_cache(cache, data)
    print("synced %s:" % args.env)
    for p in added:
        print("  added:   %s" % p)
    for p in removed:
        print("  removed: %s" % p)
    for p in changed:
        print("  changed: %s" % p)
    print("cache: %s" % cache)
    return 0


def cmd_maybe_sync(args, cache, data):
    if random.random() < args.chance:
        return cmd_sync(args, cache, data)
    print("SKIPPED (roll missed)")
    return 0


def cmd_touch(args, cache, data):
    envs = data["environments"]
    if args.env not in envs:
        print("no such env in cache: %s" % args.env, file=sys.stderr)
        return 1
    envs[args.env]["lastUsedAt"] = now_iso()
    save_cache(cache, data)
    print("touched %s" % args.env)
    return 0


# ---------- 会话活动环境 ----------

def cmd_active_get(args, cache, data):
    """输出当前会话(窗口)的活动环境名；无则输出 NONE。"""
    sid = args.session
    s = data["session"].get(sid)
    env = None
    if isinstance(s, dict):
        env = s.get("env")
    if not env:
        print("NONE")
        return 1
    print(env)
    return 0


def cmd_active_set(args, cache, data):
    """记录本次会话(窗口)的活动环境，后续调用直接复用。"""
    sid = args.session
    s = data["session"].get(sid)
    if not isinstance(s, dict):
        s = {}
    s["env"] = args.env
    # 会话从 Windows 命名空间取，天然区分不同窗口
    s["window"] = args.window or "unknown"
    s["updatedAt"] = now_iso()
    data["session"][sid] = s
    save_cache(cache, data)
    print("active env for session %s = %s" % (args.session, args.env))
    return 0


# ---------- 入口 ----------

def main(argv=None):
    _utf8_stdio()
    p = argparse.ArgumentParser(
        description="run-python 环境缓存管理器（仅标准库）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--cache", help="覆盖缓存文件路径")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="列出缓存中的环境")

    g = sub.add_parser("get", help="输出单个环境条目(JSON)")
    g.add_argument("env")

    g = sub.add_parser("match", help="列出缓存中已覆盖全部必需包的环境")
    g.add_argument("--requires", nargs="+", required=True)

    g = sub.add_parser("name-for", help="按依赖集输出确定性环境名 runpy-<hash8>")
    g.add_argument("--requires", nargs="+", required=True)

    g = sub.add_parser("add", help="新建/覆盖环境条目（新建环境首次强制记录）")
    g.add_argument("env")
    g.add_argument("--python")
    g.add_argument("--path")
    g.add_argument("--created-for")
    g.add_argument("--install-command")
    g.add_argument("--from-file")

    g = sub.add_parser("sync", help="用 pip freeze 文件对比并更新条目")
    g.add_argument("env")
    g.add_argument("--from-file", required=True)

    g = sub.add_parser("maybe-sync", help="以 chance 概率执行 sync")
    g.add_argument("env")
    g.add_argument("--from-file", required=True)
    g.add_argument("--chance", type=float, default=0.1)

    g = sub.add_parser("touch", help="更新 lastUsedAt")
    g.add_argument("env")

    g = sub.add_parser("active", help="查看/设置本会话窗口的活动环境")
    g.add_argument("env", nargs="?", help="设置时给出环境名；不带参数为查询")
    g.add_argument("--session", default="default", help="会话窗口 id（默认 default）")
    g.add_argument("--window", help="窗口标识（设置时记录，便于区分）")

    args = p.parse_args(argv)
    cache = resolve_cache(args.cache)
    data = load_cache(cache)

    handlers = {
        "list": cmd_list,
        "get": cmd_get,
        "match": cmd_match,
        "name-for": cmd_name_for,
        "add": cmd_add,
        "sync": cmd_sync,
        "maybe-sync": cmd_maybe_sync,
        "touch": cmd_touch,
        "active": cmd_active_set if args.cmd == "active" and args.env else cmd_active_get,
    }
    return handlers[args.cmd](args, cache, data)


if __name__ == "__main__":
    sys.exit(main())
