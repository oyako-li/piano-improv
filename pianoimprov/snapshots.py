"""覚えた好みをしまっておく所（SQLite、data/taste.db）。

好みは記録（ratings.jsonl）から作り直せるので、ここにしまうのは「名前を付けた好みの写し」。
写しには mu と確かさ（prec）、教えてもらった回数、覚えた曲が入る。呼び出すのは Taste.load。

  snapshots(id, name, created, upto, state)
    created  しまった時刻（epoch 秒）
    upto     どの時点の好みか（記録の時刻。いまの好みなら最後の出来事の時刻）
    state    {"mu": {...}, "prec": {...}, "n": 4, "refs": [...]}（JSON）
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path


class Snapshots:
    def __init__(self, data_dir: Path):
        self.path = data_dir / "taste.db"
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, created REAL NOT NULL,
                upto REAL, state TEXT NOT NULL)""")

    def _db(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=5)

    def add(self, name: str, state: dict) -> int:
        st = {k: state[k] for k in ("mu", "prec", "n", "refs")}
        with self._db() as db:
            cur = db.execute("INSERT INTO snapshots (name, created, upto, state) VALUES (?, ?, ?, ?)",
                             (name, time.time(), state.get("ts"), json.dumps(st, ensure_ascii=False)))
            return int(cur.lastrowid)

    def list(self) -> list[dict]:
        with self._db() as db:
            rows = db.execute("SELECT id, name, created, upto, state FROM snapshots ORDER BY id DESC").fetchall()
        out = []
        for i, name, created, upto, st in rows:
            s = json.loads(st)
            out.append({"id": i, "name": name, "created": created, "upto": upto, "n": s.get("n", 0),
                        "refs": len(s.get("refs", [])), "mu": s["mu"]})
        return out

    def get(self, sid: int) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT name, state FROM snapshots WHERE id = ?", (sid,)).fetchone()
        if not row:
            return None
        return {"name": row[0], **json.loads(row[1])}

    def delete(self, sid: int) -> bool:
        with self._db() as db:
            return db.execute("DELETE FROM snapshots WHERE id = ?", (sid,)).rowcount > 0
