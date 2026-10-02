from __future__ import annotations

import shutil
import sqlite3
import tempfile
from pathlib import Path

from .database import StateStore


def merge_databases(inputs: list[Path], output: Path) -> None:
    if not inputs:
        raise ValueError("At least one input database is required")
    first = inputs[0]
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.resolve() == first.resolve():
        with tempfile.NamedTemporaryFile(prefix="weibo-merge-", suffix=".sqlite3", dir=output.parent, delete=False) as handle:
            temporary = Path(handle.name)
        shutil.copyfile(first, temporary)
        shutil.copyfile(temporary, output)
        temporary.unlink(missing_ok=True)
    else:
        if output.exists():
            output.unlink()
        shutil.copyfile(first, output)
    destination = StateStore(output)
    try:
        for source_path in inputs[1:]:
            source = sqlite3.connect(source_path)
            source.row_factory = sqlite3.Row
            try:
                for row in source.execute("SELECT * FROM commenters"):
                    destination.conn.execute("INSERT OR IGNORE INTO commenters(parent_id,user_id,profile_url,screen_name,discovered_at) VALUES(?,?,?,?,?)", tuple(row))
                for row in source.execute("SELECT * FROM users"):
                    destination.conn.execute("""INSERT INTO users(user_id,profile_url,screen_name,gender,birth_date,education,registered_at,posts_count,followers_count,profile_fetched_at,profile_status,discovered_from)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
                    profile_url=CASE WHEN users.profile_url='' THEN excluded.profile_url ELSE users.profile_url END,
                    screen_name=CASE WHEN users.screen_name='' THEN excluded.screen_name ELSE users.screen_name END,
                    gender=CASE WHEN users.gender='' THEN excluded.gender ELSE users.gender END,
                    birth_date=CASE WHEN users.birth_date='' THEN excluded.birth_date ELSE users.birth_date END,
                    education=CASE WHEN users.education='' THEN excluded.education ELSE users.education END,
                    registered_at=CASE WHEN users.registered_at='' THEN excluded.registered_at ELSE users.registered_at END,
                    posts_count=COALESCE(users.posts_count, excluded.posts_count),
                    followers_count=COALESCE(users.followers_count, excluded.followers_count),
                    profile_fetched_at=CASE WHEN users.profile_fetched_at='' THEN excluded.profile_fetched_at ELSE users.profile_fetched_at END,
                    profile_status=CASE WHEN users.profile_status='pending' THEN excluded.profile_status ELSE users.profile_status END""", tuple(row))
                for row in source.execute("SELECT * FROM posts"):
                    destination.conn.execute("INSERT OR IGNORE INTO posts(post_id,user_id,text,location,published_at,is_original,url,fetched_at,source_parent_id) VALUES(?,?,?,?,?,?,?,?,?)", tuple(row))
                for row in source.execute("SELECT * FROM parent_posts"):
                    destination.conn.execute("INSERT OR IGNORE INTO parent_posts(parent_id,url,status,page_number,discovered_users,updated_at) VALUES(?,?,?,?,?,?)", tuple(row))
            finally:
                source.close()
        destination.conn.commit()
    finally:
        destination.close()
