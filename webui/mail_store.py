#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tempo Mail 本地邮箱库
====================
把创建的临时邮箱持久化到本地, 支持增删查、导出 CSV。

两种后端(按文件后缀自动选择):
  - SQLite(.db)  —— 推荐。上万条量级性能好, 单文件, 支持索引/事务/并发读。
  - JSON(.json)  —— 默认兼容旧数据。简单直观, 但上万条全量重写会明显变慢。

默认存储路径: ~/.tempo_mail_store.json
用法:
  store = MailStore()                       # JSON 后端(默认)
  store = MailStore("mails.db")             # SQLite 后端(自动识别 .db)
  store.add(email=..., domain=..., email_token=..., app_token=..., expire_at=...)
  store.add_many([{...}, {...}])            # 批量写入(事务, 上万条推荐)
  store.list_all() / get(email) / remove(email) / count()
  store.export_csv("out.csv")
"""
import csv
import json
import os
import sqlite3
import threading
import time


DEFAULT_PATH = os.path.join(os.path.expanduser("~"),
                            ".tempo_mail_store.json")

# 记录字段(顺序即 CSV 导出列顺序)
FIELDS = ["email", "domain", "email_token", "app_token",
          "source", "env", "created_at", "expire_at", "updated_at"]


class MailStore:
    """统一入口。path 以 .db 结尾自动启用 SQLite, 否则 JSON。"""

    def __init__(self, path=DEFAULT_PATH):
        self.path = path
        if path.lower().endswith(".db"):
            self._backend = _SqliteBackend(path)
        else:
            self._backend = _JsonBackend(path)

    # ---------- 统一接口 ----------
    def add(self, email, domain=None, email_token=None, app_token=None,
            expire_at=None, source="custom", env="web"):
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        rec = {"email": email, "domain": domain or "",
               "email_token": email_token or "",
               "app_token": app_token or "",
               "source": source, "env": env,
               "created_at": now, "expire_at": expire_at or "",
               "updated_at": now}
        return self._backend.upsert(rec)

    def add_many(self, records, batch=500):
        """批量写入多条记录(事务, 分批 commit)。records 为 dict 列表。
        已存在的 email 会被更新。返回写入条数。"""
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        norm = []
        for r in records:
            rec = {"email": r.get("email", ""),
                   "domain": r.get("domain") or "",
                   "email_token": r.get("email_token") or "",
                   "app_token": r.get("app_token") or "",
                   "source": r.get("source") or "random",
                   "env": r.get("env") or "api",
                   "created_at": r.get("created_at") or now,
                   "expire_at": r.get("expire_at") or "",
                   "updated_at": now}
            if rec["email"]:
                norm.append(rec)
        return self._backend.upsert_many(norm, batch=batch)

    def get(self, email):
        return self._backend.get(email)

    def list_all(self):
        return self._backend.list_all()

    def list_paginated(self, limit=20, offset=0, search=""):
        return self._backend.list_paginated(limit, offset, search)

    def remove(self, email):
        return self._backend.remove(email)

    def clear(self):
        return self._backend.clear()

    def count(self, search=""):
        return self._backend.count(search=search)

    def export_csv(self, out_path):
        rows = self._backend.list_all()
        if not rows:
            raise ValueError("库为空, 无内容可导出")
        with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in FIELDS})
        return out_path


# ============================== JSON 后端 ==============================
class _JsonBackend:
    def __init__(self, path):
        self.path = path
        self._data = self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, list):
                    return raw
                if isinstance(raw, dict) and "mails" in raw:
                    return raw["mails"]
            except Exception:
                pass
        return []

    def _save(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)),
                    exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"mails": self._data}, f, ensure_ascii=False, indent=2)

    def upsert(self, rec):
        for i, r in enumerate(self._data):
            if r.get("email") == rec["email"]:
                rec["created_at"] = r.get("created_at") or rec["created_at"]
                self._data[i] = rec
                self._save()
                return rec
        self._data.append(rec)
        self._save()
        return rec

    def upsert_many(self, records, batch=500):
        # 去重: 后写覆盖先写
        merged = {}
        for r in records:
            merged[r["email"]] = r
        added = 0
        for rec in merged.values():
            self.upsert(rec)
            added += 1
        return added

    def get(self, email):
        for r in self._data:
            if r.get("email") == email:
                return r
        return None

    def list_all(self):
        return list(self._data)

    def list_paginated(self, limit=20, offset=0, search=""):
        items = self._data
        if search:
            s = search.lower().strip()
            items = [r for r in items if s in (r.get("email") or "").lower()]
        return list(items[offset:offset + limit])

    def remove(self, email):
        original_len = len(self._data)
        self._data = [r for r in self._data if r.get("email") != email]
        if len(self._data) == original_len:
            return False
        self._save()
        return True

    def clear(self):
        self._data = []
        self._save()

    def count(self, search=""):
        if search:
            s = search.lower().strip()
            return len([r for r in self._data if s in (r.get("email") or "").lower()])
        return len(self._data)


# ============================== SQLite 后端 ==============================
_SQLITE_WRITE_LOCKS = {}
_SQLITE_WRITE_LOCKS_GUARD = threading.Lock()


class _SqliteBackend:
    def __init__(self, path):
        self.path = path
        lock_key = os.path.abspath(path)
        with _SQLITE_WRITE_LOCKS_GUARD:
            self._write_lock = _SQLITE_WRITE_LOCKS.setdefault(
                lock_key, threading.RLock())
        self._conn = sqlite3.connect(path, timeout=30)
        self._conn.row_factory = sqlite3.Row
        with self._write_lock:
            self._conn.execute("PRAGMA busy_timeout=30000")
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._init_schema()

    def _init_schema(self):
        cur = self._conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mails (
                email       TEXT PRIMARY KEY,
                domain      TEXT DEFAULT '',
                email_token TEXT DEFAULT '',
                app_token   TEXT DEFAULT '',
                source      TEXT DEFAULT '',
                env         TEXT DEFAULT '',
                created_at  TEXT DEFAULT '',
                expire_at   TEXT DEFAULT '',
                updated_at  TEXT DEFAULT ''
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_expire ON mails(expire_at)")
        self._conn.commit()

    def upsert(self, rec):
        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                # 保留已存在的 created_at, and do the read and write atomically.
                cur = self._conn.cursor()
                cur.execute("SELECT created_at FROM mails WHERE email=?", (rec["email"],))
                row = cur.fetchone()
                if row and row["created_at"]:
                    rec["created_at"] = row["created_at"]
                self._execute_upsert(rec)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return rec

    def _execute_upsert(self, rec):
        self._conn.execute("""
            INSERT INTO mails (email, domain, email_token, app_token,
                               source, env, created_at, expire_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(email) DO UPDATE SET
                domain=excluded.domain, email_token=excluded.email_token,
                app_token=excluded.app_token, source=excluded.source,
                env=excluded.env, created_at=excluded.created_at,
                expire_at=excluded.expire_at, updated_at=excluded.updated_at
        """, (rec["email"], rec["domain"], rec["email_token"], rec["app_token"],
              rec["source"], rec["env"], rec["created_at"], rec["expire_at"],
              rec["updated_at"]))

    def upsert_many(self, records, batch=500):
        added = 0
        with self._write_lock:
            for i in range(0, len(records), batch):
                chunk = records[i:i + batch]
                self._conn.execute("BEGIN IMMEDIATE")
                try:
                    for rec in chunk:
                        self._execute_upsert(rec)
                    self._conn.commit()
                    added += len(chunk)
                except Exception:
                    self._conn.rollback()
                    raise
        return added

    def get(self, email):
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM mails WHERE email=?", (email,))
        row = cur.fetchone()
        return dict(row) if row else None

    def list_all(self):
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM mails")
        return [dict(r) for r in cur.fetchall()]

    def list_paginated(self, limit=20, offset=0, search=""):
        cur = self._conn.cursor()
        if search:
            like = f"%{search.replace('%', '%%')}%"
            cur.execute(
                "SELECT * FROM mails WHERE email LIKE ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (like, limit, offset))
        else:
            cur.execute("SELECT * FROM mails ORDER BY created_at DESC LIMIT ? OFFSET ?",
                        (limit, offset))
        return [dict(r) for r in cur.fetchall()]

    def remove(self, email):
        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cur = self._conn.cursor()
                cur.execute("DELETE FROM mails WHERE email=?", (email,))
                removed = cur.rowcount > 0
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return removed

    def clear(self):
        with self._write_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute("DELETE FROM mails")
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def count(self, search=""):
        cur = self._conn.cursor()
        if search:
            like = f"%{search.replace('%', '%%')}%"
            cur.execute("SELECT COUNT(*) AS n FROM mails WHERE email LIKE ?", (like,))
        else:
            cur.execute("SELECT COUNT(*) AS n FROM mails")
        return cur.fetchone()["n"]


def print_table(records, show_token=True):
    """终端友好打印邮箱列表。"""
    if not records:
        print("   (空)")
        return
    hdr = f"  {'邮箱':<38} {'域名':<18} {'创建时间':<20} {'来源':<8}"
    if show_token:
        hdr += f" {'token(前12)':<14}"
    print(hdr)
    print("  " + "-" * (min(len(hdr) + 6, 120)))
    for r in records:
        line = (f"  {r.get('email',''):<40} {str(r.get('domain','')):<18}"
                f" {str(r.get('created_at','')):<20} {str(r.get('source','')):<8}")
        if show_token:
            line += f" {str(r.get('email_token',''))[:12]:<14}"
        print(line)
