"""Read-only diagnosis: list model_provider/model distribution in Codex sessions and state db."""
import collections
import glob
import json
import os
import sqlite3

home = os.path.expanduser("~/.codex")

cfg = os.path.join(home, "config.toml")
if os.path.exists(cfg):
    with open(cfg, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            s = line.strip()
            if s.startswith("model_provider"):
                print("config.toml:", s)
                break
            if s.startswith("["):
                break

counter = collections.Counter()
files_by_provider = collections.defaultdict(set)
for d in ["sessions", "archived_sessions"]:
    for fp in glob.glob(os.path.join(home, d, "**", "*.jsonl"), recursive=True):
        try:
            with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if "model_provider" not in line or "session_meta" not in line:
                        continue
                    try:
                        data = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(data, dict) or data.get("type") != "session_meta":
                        continue
                    payload = data.get("payload")
                    if isinstance(payload, dict) and payload.get("model_provider") is not None:
                        p = payload["model_provider"]
                        counter[p] += 1
                        files_by_provider[p].add(fp)
        except Exception as e:
            print("ERR reading", fp, e)

print("session_meta model_provider counts:", dict(counter))
for p, files in sorted(files_by_provider.items()):
    print(f"  provider={p!r}: {len(files)} files")

db = os.path.join(home, "state_5.sqlite")
if not os.path.exists(db):
    print("state_5.sqlite not found at", db)
else:
    try:
        con = sqlite3.connect("file:" + db.replace("\\", "/") + "?mode=ro", uri=True)
        cur = con.cursor()
        cols = [r[1] for r in cur.execute("PRAGMA table_info(threads)")]
        if "model_provider" in cols:
            rows = cur.execute(
                "SELECT model_provider, model, COUNT(*) FROM threads GROUP BY model_provider, model"
            ).fetchall()
            print("threads (provider | model | count):")
            for p, m, c in rows:
                print(f"  {p!r} | {m!r} | {c}")
        con.close()
    except Exception as e:
        print("DB ERR:", e)
