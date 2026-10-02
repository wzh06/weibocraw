from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from .models import Checkpoint, CommenterRef, ParentPostConfig, PostRecord, UserRecord


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class StateStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.migrate()

    def migrate(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS parent_posts (
                parent_id TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                page_number INTEGER NOT NULL DEFAULT 1,
                discovered_users INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS commenters (
                parent_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                profile_url TEXT NOT NULL DEFAULT '',
                screen_name TEXT NOT NULL DEFAULT '',
                discovered_at TEXT NOT NULL,
                PRIMARY KEY(parent_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                profile_url TEXT NOT NULL DEFAULT '',
                screen_name TEXT NOT NULL DEFAULT '',
                gender TEXT NOT NULL DEFAULT '',
                birth_date TEXT NOT NULL DEFAULT '',
                education TEXT NOT NULL DEFAULT '',
                registered_at TEXT NOT NULL DEFAULT '',
                posts_count INTEGER,
                followers_count INTEGER,
                profile_fetched_at TEXT NOT NULL DEFAULT '',
                profile_status TEXT NOT NULL DEFAULT 'pending',
                discovered_from TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS posts (
                post_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                text TEXT NOT NULL,
                location TEXT NOT NULL DEFAULT '',
                published_at TEXT NOT NULL DEFAULT '',
                is_original INTEGER,
                url TEXT NOT NULL DEFAULT '',
                fetched_at TEXT NOT NULL,
                source_parent_id TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS checkpoints (
                scope TEXT PRIMARY KEY,
                cursor TEXT NOT NULL DEFAULT '',
                page_number INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS account_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_name TEXT NOT NULL,
                event_type TEXT NOT NULL,
                message TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def upsert_parent_post(self, parent: ParentPostConfig) -> None:
        self.conn.execute(
            """INSERT INTO parent_posts(parent_id,url,updated_at) VALUES(?,?,?)
            ON CONFLICT(parent_id) DO UPDATE SET url=excluded.url""",
            (parent.parent_id, parent.url, utc_now()),
        )
        self.conn.commit()

    def get_parent_checkpoint(self, parent_id: str) -> Checkpoint:
        row = self.conn.execute("SELECT page_number, updated_at FROM parent_posts WHERE parent_id=?", (parent_id,)).fetchone()
        if row:
            return Checkpoint(parent_id, page_number=int(row[0]), updated_at=row[1])
        row = self.conn.execute("SELECT page_number, cursor, updated_at FROM checkpoints WHERE scope=?", (f"parent:{parent_id}",)).fetchone()
        return Checkpoint(f"parent:{parent_id}", row[1], int(row[0]), row[2]) if row else Checkpoint(f"parent:{parent_id}")

    def set_parent_checkpoint(self, parent_id: str, page_number: int, cursor: str = "") -> None:
        now = utc_now()
        self.conn.execute("UPDATE parent_posts SET page_number=?, updated_at=? WHERE parent_id=?", (page_number, now, parent_id))
        self.conn.execute(
            """INSERT INTO checkpoints(scope,cursor,page_number,updated_at) VALUES(?,?,?,?)
            ON CONFLICT(scope) DO UPDATE SET cursor=excluded.cursor,page_number=excluded.page_number,updated_at=excluded.updated_at""",
            (f"parent:{parent_id}", cursor, page_number, now),
        )
        self.conn.commit()

    def mark_parent_finished(self, parent_id: str) -> None:
        self.conn.execute("UPDATE parent_posts SET status='finished', updated_at=? WHERE parent_id=?", (utc_now(), parent_id))
        self.conn.commit()

    def is_parent_finished(self, parent_id: str) -> bool:
        row = self.conn.execute("SELECT status FROM parent_posts WHERE parent_id=?", (parent_id,)).fetchone()
        return bool(row and row[0] == "finished")

    def upsert_commenter(self, ref: CommenterRef) -> bool:
        cursor = self.conn.execute(
            "INSERT OR IGNORE INTO commenters(parent_id,user_id,profile_url,screen_name,discovered_at) VALUES(?,?,?,?,?)",
            (ref.parent_id, ref.user_id, ref.profile_url, ref.screen_name, utc_now()),
        )
        self.conn.execute(
            """INSERT INTO users(user_id,profile_url,screen_name,discovered_from)
            VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
            profile_url=CASE WHEN users.profile_url='' THEN excluded.profile_url ELSE users.profile_url END,
            screen_name=CASE WHEN users.screen_name='' THEN excluded.screen_name ELSE users.screen_name END,
            discovered_from=CASE WHEN users.discovered_from='' THEN excluded.discovered_from ELSE users.discovered_from END""",
            (ref.user_id, ref.profile_url, ref.screen_name, ref.parent_id),
        )
        self.conn.commit()
        return cursor.rowcount == 1

    def upsert_user(self, user: UserRecord) -> bool:
        before = self.conn.execute("SELECT user_id FROM users WHERE user_id=?", (user.user_id,)).fetchone()
        self.conn.execute(
            """INSERT INTO users(user_id,profile_url,screen_name,gender,birth_date,education,registered_at,posts_count,followers_count,profile_fetched_at,profile_status,discovered_from)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
            profile_url=CASE WHEN excluded.profile_url='' THEN users.profile_url ELSE excluded.profile_url END,
            screen_name=CASE WHEN excluded.screen_name='' THEN users.screen_name ELSE excluded.screen_name END,
            gender=CASE WHEN excluded.gender='' THEN users.gender ELSE excluded.gender END,
            birth_date=CASE WHEN excluded.birth_date='' THEN users.birth_date ELSE excluded.birth_date END,
            education=CASE WHEN excluded.education='' THEN users.education ELSE excluded.education END,
            registered_at=CASE WHEN excluded.registered_at='' THEN users.registered_at ELSE excluded.registered_at END,
            posts_count=COALESCE(excluded.posts_count, users.posts_count),
            followers_count=COALESCE(excluded.followers_count, users.followers_count),
            profile_fetched_at=CASE WHEN excluded.profile_fetched_at='' THEN users.profile_fetched_at ELSE excluded.profile_fetched_at END,
            profile_status=excluded.profile_status""",
            tuple(asdict(user).values()),
        )
        self.conn.commit()
        return before is None

    def get_user(self, user_id: str) -> UserRecord | None:
        row = self.conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        return UserRecord(**dict(row)) if row else None

    def get_pending_user_ids(self) -> list[str]:
        rows = self.conn.execute("SELECT user_id FROM users WHERE profile_status='pending' ORDER BY user_id").fetchall()
        return [str(row[0]) for row in rows]

    def get_eligible_users(self) -> list[UserRecord]:
        rows = self.conn.execute("SELECT * FROM users WHERE profile_status='success' ORDER BY user_id").fetchall()
        return [UserRecord(**dict(row)) for row in rows]

    def upsert_post(self, post: PostRecord) -> bool:
        cursor = self.conn.execute(
            """INSERT OR IGNORE INTO posts(post_id,user_id,text,location,published_at,is_original,url,fetched_at,source_parent_id)
            VALUES(?,?,?,?,?,?,?,?,?)""",
            (post.post_id, post.user_id, post.text, post.location, post.published_at, None if post.is_original is None else int(post.is_original), post.url, post.fetched_at, post.source_parent_id),
        )
        self.conn.commit()
        return cursor.rowcount == 1

    def get_post_checkpoint(self, user_id: str) -> Checkpoint:
        row = self.conn.execute("SELECT page_number,cursor,updated_at FROM checkpoints WHERE scope=?", (f"user:{user_id}",)).fetchone()
        return Checkpoint(f"user:{user_id}", row[1], int(row[0]), row[2]) if row else Checkpoint(f"user:{user_id}")

    def set_post_checkpoint(self, user_id: str, page_number: int, cursor: str = "") -> None:
        self.conn.execute(
            """INSERT INTO checkpoints(scope,cursor,page_number,updated_at) VALUES(?,?,?,?)
            ON CONFLICT(scope) DO UPDATE SET cursor=excluded.cursor,page_number=excluded.page_number,updated_at=excluded.updated_at""",
            (f"user:{user_id}", cursor, page_number, utc_now()),
        )
        self.conn.commit()

    def count_users(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])

    def count_posts(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0])

    def log_account_event(self, account_name: str, event_type: str, message: str = "") -> None:
        self.conn.execute("INSERT INTO account_events(account_name,event_type,message,created_at) VALUES(?,?,?,?)", (account_name, event_type, message, utc_now()))
        self.conn.commit()

    def export_users_basic_csv(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fields = ["user_id", "gender", "birth_date", "education", "registered_at", "posts_count", "followers_count"]
        rows = self.conn.execute("SELECT user_id,gender,birth_date,education,registered_at,posts_count,followers_count FROM users WHERE profile_status='success' ORDER BY user_id")
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(dict(row) for row in rows)

    def export_posts_content_csv(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fields = ["user_id", "text", "location", "published_at", "is_original"]
        rows = self.conn.execute("SELECT user_id,text,location,published_at,is_original FROM posts ORDER BY user_id,published_at,post_id")
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                value = dict(row)
                value["is_original"] = "是" if value["is_original"] == 1 else "否" if value["is_original"] == 0 else ""
                writer.writerow(value)
