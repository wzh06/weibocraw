# Weibo comment-participant collector

This is a local Python + Playwright collector for public Weibo pages visible to
your normal logged-in browser. It does not bypass logins, verification
challenges, rate limits, or access controls.

## Setup

    uv sync
    uv run playwright install chromium
    cp config.example.yaml config.yaml

Edit `config.yaml` with the mother-post URLs/IDs and the account profiles.

## Login and run

    uv run python crawler.py login --config config.yaml --account all
    uv run python crawler.py crawl --config config.yaml

The login command opens a normal browser window. Complete login yourself, then
press Enter in the terminal. The crawl command resumes from SQLite checkpoints,
deduplicates users and posts, and stops at 12,000 unique content rows by
default.

When an account is rate-limited or shown a verification page, the program saves
state and asks you to choose another already logged-in account. It never
attempts to bypass the verification.

## Exports

- `data/users_basic.csv`: `user_id, gender, birth_date, education,
  registered_at, posts_count, followers_count`
- `data/posts_content.csv`: `user_id, text, location, published_at,
  is_original`

## Multiple devices

Use different `device_id` values, local SQLite files, and disjoint mother-post
lists. Merge after collection:

    uv run python crawler.py merge-db --output data/merged.sqlite3 \
      data/state-mac-01.sqlite3 data/state-linux-01.sqlite3

Run at a conservative rate and only collect information you are permitted to
access and process under Weibo's terms and applicable law.



- 新增详细的 AGENTS.md
- 新增 `weibo_crawler/` 模块化架构：
  - 配置解析
  - Playwright 登录与账户切换
  - 母帖评论用户采集
  - 用户资料解析与筛选
  - 用户微博采集
  - SQLite 去重与断点续爬
  - CSV 导出
  - 多设备数据库合并
- 新增 `pyproject.toml`，支持 `uv`
- 更新 config.example.yaml
- 更新 README.md
- `crawler.py` 保留为兼容入口
- 新增数据库、筛选、解析和合并测试

命令：

```
uv sync
uv run playwright install chromium

uv run python crawler.py login --config config.yaml --account all
uv run python crawler.py crawl --config config.yaml
uv run python crawler.py export --config config.yaml
```

多设备合并：

```
uv run python crawler.py merge-db \
  --output data/merged.sqlite3 \
  data/state-mac-01.sqlite3 \
  data/state-linux-01.sqlite3
```

验证结果：

- `python3 -m py_compile ...` 通过
- 7 个单元测试全部通过
- `git diff --check` 通过
- 数据库去重、CSV 导出、筛选和数据库合并已完成冒烟测试