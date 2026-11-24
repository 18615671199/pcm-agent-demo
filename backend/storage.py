"""简单 SQLite 持久化 — 风险识别 / 质量检查 AI 结果留存"""
from __future__ import annotations
import json, os, sqlite3, threading
from datetime import datetime

DB_PATH = os.environ.get("DB_PATH") or "/tmp/pcm_records.db"
_lock = threading.Lock()
_conn_instance = None


def _get_conn():
    global _conn_instance
    if _conn_instance is None:
        _conn_instance = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn_instance.row_factory = sqlite3.Row
        _conn_instance.execute("PRAGMA journal_mode=WAL")
    return _conn_instance


def ensure_tables():
    with _lock:
        c = _get_conn()
        c.executescript("""
        CREATE TABLE IF NOT EXISTS risk_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            external_id TEXT UNIQUE,
            created_at TEXT NOT NULL,
            input_json TEXT NOT NULL,
            output_json TEXT NOT NULL,
            project TEXT,
            work_type TEXT,
            work_name TEXT
        );
        CREATE TABLE IF NOT EXISTS quality_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            external_id TEXT UNIQUE,
            created_at TEXT NOT NULL,
            input_json TEXT NOT NULL,
            output_json TEXT NOT NULL,
            project TEXT,
            work_type TEXT,
            work_name TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_risk_created ON risk_records(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_quality_created ON quality_records(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_quality_external ON quality_records(external_id);
        CREATE INDEX IF NOT EXISTS idx_risk_external ON risk_records(external_id);
        """)
        # 兼容旧表：如果 external_id 列不存在，ALTER 添加
        try:
            c.execute("ALTER TABLE quality_records ADD COLUMN external_id TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE risk_records ADD COLUMN external_id TEXT")
        except sqlite3.OperationalError:
            pass
        c.commit()


def save_risk(input_obj, output_obj, external_id: str | None = None) -> int:
    ensure_tables()
    d = input_obj if isinstance(input_obj, dict) else input_obj.model_dump()
    out = output_obj if isinstance(output_obj, dict) else output_obj
    with _lock:
        c = _get_conn()
        if external_id:
            # 有 external_id → UNIQUE 约束，重复生成直接覆盖
            cur = c.execute("SELECT id FROM risk_records WHERE external_id=?", (external_id,))
            existing = cur.fetchone()
            if existing:
                c.execute(
                    "UPDATE risk_records SET created_at=?, input_json=?, output_json=?, project=?, work_type=?, work_name=? WHERE external_id=?",
                    (
                        datetime.now().isoformat(timespec="seconds"),
                        json.dumps(d, ensure_ascii=False),
                        json.dumps(out, ensure_ascii=False),
                        d.get("project", ""),
                        d.get("work_type", ""),
                        d.get("work_name", ""),
                        external_id,
                    ),
                )
                c.commit()
                return existing["id"]
        cur = c.execute(
            "INSERT INTO risk_records(external_id,created_at,input_json,output_json,project,work_type,work_name) VALUES (?,?,?,?,?,?,?)",
            (
                external_id,
                datetime.now().isoformat(timespec="seconds"),
                json.dumps(d, ensure_ascii=False),
                json.dumps(out, ensure_ascii=False),
                d.get("project", ""),
                d.get("work_type", ""),
                d.get("work_name", ""),
            ),
        )
        c.commit()
        return cur.lastrowid


def save_quality(input_obj, output_obj, external_id: str | None = None) -> int:
    ensure_tables()
    d = input_obj if isinstance(input_obj, dict) else input_obj.model_dump()
    out = output_obj if isinstance(output_obj, dict) else output_obj
    with _lock:
        c = _get_conn()
        if external_id:
            cur = c.execute("SELECT id FROM quality_records WHERE external_id=?", (external_id,))
            existing = cur.fetchone()
            if existing:
                c.execute(
                    "UPDATE quality_records SET created_at=?, input_json=?, output_json=?, project=?, work_type=?, work_name=? WHERE external_id=?",
                    (
                        datetime.now().isoformat(timespec="seconds"),
                        json.dumps(d, ensure_ascii=False),
                        json.dumps(out, ensure_ascii=False),
                        d.get("project", ""),
                        d.get("work_type", ""),
                        d.get("work_name", ""),
                        external_id,
                    ),
                )
                c.commit()
                return existing["id"]
        cur = c.execute(
            "INSERT INTO quality_records(external_id,created_at,input_json,output_json,project,work_type,work_name) VALUES (?,?,?,?,?,?,?)",
            (
                external_id,
                datetime.now().isoformat(timespec="seconds"),
                json.dumps(d, ensure_ascii=False),
                json.dumps(out, ensure_ascii=False),
                d.get("project", ""),
                d.get("work_type", ""),
                d.get("work_name", ""),
            ),
        )
        c.commit()
        return cur.lastrowid


def list_risk(limit: int = 50) -> list:
    ensure_tables()
    c = _get_conn()
    rows = c.execute(
        "SELECT id,created_at,project,work_type,work_name FROM risk_records ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_quality(limit: int = 50) -> list:
    ensure_tables()
    c = _get_conn()
    rows = c.execute(
        "SELECT id,created_at,project,work_type,work_name FROM quality_records ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_risk(rid: int) -> dict | None:
    ensure_tables()
    c = _get_conn()
    r = c.execute("SELECT * FROM risk_records WHERE id=?", (rid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["input"] = json.loads(d.pop("input_json"))
    d["output"] = json.loads(d.pop("output_json"))
    return d


def get_quality(rid: int) -> dict | None:
    ensure_tables()
    c = _get_conn()
    r = c.execute("SELECT * FROM quality_records WHERE id=?", (rid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["input"] = json.loads(d.pop("input_json"))
    d["output"] = json.loads(d.pop("output_json"))
    return d


def find_quality_by_external_id(external_id: str) -> dict | None:
    """按集成方传入的 external_id 查询质量检查表是否已生成"""
    ensure_tables()
    c = _get_conn()
    r = c.execute("SELECT * FROM quality_records WHERE external_id=?", (external_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["input"] = json.loads(d.pop("input_json"))
    d["output"] = json.loads(d.pop("output_json"))
    return d


def find_risk_by_external_id(external_id: str) -> dict | None:
    """按集成方传入的 external_id 查询风险识别是否已生成"""
    ensure_tables()
    c = _get_conn()
    r = c.execute("SELECT * FROM risk_records WHERE external_id=?", (external_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["input"] = json.loads(d.pop("input_json"))
    d["output"] = json.loads(d.pop("output_json"))
    return d
