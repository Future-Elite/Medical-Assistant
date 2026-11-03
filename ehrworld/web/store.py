from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from ehrworld.io import output_path


def now():
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    def __init__(self, path: str | Path):
        self.path = output_path(path)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS batches (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL)")

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=20)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def save(self, job):
        job["updated_at"] = now()
        with self.connect() as db:
            db.execute("INSERT INTO jobs VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                       (job["id"], job["created_at"], json.dumps(job, ensure_ascii=False, allow_nan=False)))

    def get(self, identifier):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM jobs WHERE id=?", (identifier,)).fetchone()
        return json.loads(row[0]) if row else None

    def list(self, limit=30):
        with self.connect() as db:
            rows = db.execute("SELECT payload FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def interrupt_unfinished(self):
        with self.connect() as db:
            rows = db.execute("SELECT payload FROM jobs").fetchall()
        for row in rows:
            job = json.loads(row[0])
            if job["status"] in ("queued", "running"):
                job["status"] = "failed"
                job["error"] = {"code": "server_restarted", "message": "服务已重启，上次未完成的任务已停止。可重新生成。", "retryable": True}
                self.save(job)

    def create_batch(self, batch, jobs):
        with self.connect() as db:
            db.execute("INSERT INTO batches VALUES (?,?,?)", (batch['id'], batch['created_at'], json.dumps(batch, ensure_ascii=False, allow_nan=False)))
            db.executemany("INSERT INTO jobs VALUES (?,?,?)", [(j['id'], j['created_at'], json.dumps(j, ensure_ascii=False, allow_nan=False)) for j in jobs])

    def get_batch(self, identifier):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM batches WHERE id=?", (identifier,)).fetchone()
        return json.loads(row[0]) if row else None

    def list_batches(self, limit=30):
        with self.connect() as db:
            rows = db.execute("SELECT payload FROM batches ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def batch_jobs(self, batch):
        ids = batch['job_ids']
        with self.connect() as db:
            rows = db.execute('SELECT id,payload FROM jobs WHERE id IN (' + ','.join('?' for _ in ids) + ')', ids).fetchall()
        mapping = {row[0]: json.loads(row[1]) for row in rows}
        return [mapping[i] for i in ids]

    def replace_batch_job(self, batch_id, previous_id, job):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            batch = json.loads(db.execute('SELECT payload FROM batches WHERE id=?', (batch_id,)).fetchone()[0])
            if previous_id not in batch['job_ids']:
                raise ValueError('该患者已有新的重试任务，请刷新批次。')
            batch['job_ids'][batch['job_ids'].index(previous_id)] = job['id']
            db.execute('INSERT INTO jobs VALUES (?,?,?)', (job['id'],job['created_at'],json.dumps(job,ensure_ascii=False,allow_nan=False)))
            db.execute('UPDATE batches SET payload=? WHERE id=?', (json.dumps(batch,ensure_ascii=False),batch_id))
