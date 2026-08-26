"""Stage fixed Codex files (sessions + state_5.sqlite) into ./staging for PowerShell copy-back.

Never writes to ~/.codex directly (TRAE sandbox blocks non-PowerShell writes there).
Usage:
  python migrate.py --new kapibala --old OpenAI --old deepseek
  python migrate.py --new X --old Y --fix-model old-model=new-model
"""
import argparse
import glob
import json
import os
import shutil
import sqlite3

parser = argparse.ArgumentParser()
parser.add_argument("--new", required=True, help="new provider name (the one now in config.toml)")
parser.add_argument("--old", action="append", required=True, help="old provider name(s), repeatable")
parser.add_argument("--fix-model", action="append", default=[],
                    help="model rename OLD=NEW for models the new relay does not serve, repeatable")
args = parser.parse_args()

NEW = args.new
OLD = set(args.old)
MODEL_FIXES = []
for item in args.fix_model:
    if "=" not in item:
        raise SystemExit("--fix-model expects OLD=NEW, got: " + item)
    old_m, new_m = item.split("=", 1)
    MODEL_FIXES.append((old_m, new_m))

home = os.path.expanduser("~/.codex")
stage = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "staging")
stage = os.path.normpath(stage)
if os.path.exists(stage):
    shutil.rmtree(stage)
os.makedirs(stage)

fixed_files = 0
fixed_records = 0
for d in ["sessions", "archived_sessions"]:
    for fp in glob.glob(os.path.join(home, d, "**", "*.jsonl"), recursive=True):
        changed = False
        out = []
        with open(fp, "r", encoding="utf-8", errors="surrogateescape") as f:
            for line in f:
                if "model_provider" in line and "session_meta" in line:
                    try:
                        data = json.loads(line)
                    except Exception:
                        out.append(line)
                        continue
                    payload = data.get("payload") if isinstance(data, dict) else None
                    if data.get("type") == "session_meta" and isinstance(payload, dict) \
                            and payload.get("model_provider") in OLD:
                        payload["model_provider"] = NEW
                        data["payload"] = payload
                        changed = True
                        fixed_records += 1
                        out.append(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n")
                        continue
                out.append(line)
        if changed:
            rel = os.path.relpath(fp, home)
            dst = os.path.join(stage, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
                f.writelines(out)
            fixed_files += 1

print(f"session files staged: {fixed_files} | session_meta records fixed: {fixed_records}")

db = os.path.join(home, "state_5.sqlite")
if not os.path.exists(db):
    print("state_5.sqlite not found, skipped DB step")
else:
    for name in ["state_5.sqlite", "state_5.sqlite-wal", "state_5.sqlite-shm"]:
        src = os.path.join(home, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(stage, name))
    con = sqlite3.connect(os.path.join(stage, "state_5.sqlite"))
    cur = con.cursor()
    ph = ",".join("?" for _ in OLD)
    cur.execute(
        f"UPDATE threads SET model_provider = ? WHERE model_provider IN ({ph})",
        [NEW, *sorted(OLD)],
    )
    print("threads provider updated:", cur.rowcount)
    for old_m, new_m in MODEL_FIXES:
        cur.execute("UPDATE threads SET model = ? WHERE model = ?", [new_m, old_m])
        print(f"model {old_m!r} -> {new_m!r} rows:", cur.rowcount)
    con.commit()
    con.close()

print()
print("Staging ready:", stage)
print("Next: copy staged files back with PowerShell, then delete stale wal/shm:")
print('  Get-ChildItem "<staging>" -Recurse -File | ForEach-Object {')
print('    $rel = $_.FullName.Substring("<staging>".Length + 1)')
print('    Copy-Item -LiteralPath $_.FullName -Destination (Join-Path "$env:USERPROFILE\\.codex" $rel) -Force }')
print('  Remove-Item "$env:USERPROFILE\\.codex\\state_5.sqlite-wal" -Force -ErrorAction SilentlyContinue')
print('  Remove-Item "$env:USERPROFILE\\.codex\\state_5.sqlite-shm" -Force -ErrorAction SilentlyContinue')
