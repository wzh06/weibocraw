# 微博评论区参与者采集器

这是一个本地运行的 Python + Playwright 微博公开页面采集工具。它只采集已登录微博账号在正常浏览状态下可见的公开字段，不绕过登录、验证码、访问限制或隐私设置。

程序从你指定的母帖（多位博主的微博评论区）开始，抽取评论参与者，采集这些用户的公开资料和微博内容，并按照一定条件筛选后写入 SQLite 数据库。项目面向多账户、多设备场景设计，具备查重、断点续爬、CSV 导出和数据库合并能力。

> 合规提示：请只采集你被授权访问和处理的信息，遵守微博服务条款、目标网站规则及适用法律。保持保守的抓取频率，不要尝试突破验证码、登录或频控。

## 项目简介

### 主要能力

- **母帖评论用户抽样**：从一个或多个母帖页面翻页抽取评论用户链接。
- **用户资料采集**：读取公开资料中的性别、出生日期、教育信息、注册时间、微博总数、粉丝数量。
- **用户微博采集**：抓取符合条件的用户已发布的公开微博正文、位置、发布日期、是否原创。
- **自动筛选**：注册满一年、微博总数不少于 50 条、粉丝数不超过 5000 的用户才会被纳入后续微博采集和导出。
- **SQLite 去重**：对评论用户、用户资料、微博内容按唯一键去重。
- **断点续爬**：母帖翻页和用户微博翻页都有检查点，中断后可从上次进度继续。
- **多账户切换**：当某个账户触发登录、验证码或频控时，保存状态并提示切换到其他已登录账户。
- **多设备并行**：不同设备使用不同的 `device_id`、SQLite 文件和互不重叠的母帖列表，最后合并数据库。
- **CSV 与报告输出**：导出用户基本信息 CSV、微博内容 CSV，以及每次运行的结果报告 JSON。

### 默认筛选规则

程序使用 `weibo_crawler/filters.py` 中的规则判断用户是否合格：

- 注册时间满一年；
- 微博总数至少 50 条；
- 微博总数不超过配置的 `max_posts_count`（如果启用）；
- 粉丝数量不超过 5000。

注册满一年的判断基准日期由配置项 `eligibility_as_of` 控制，默认值为 `2026-10-01`。如果你需要按今天计算，可将其改为运行日期，但要注意这会降低多次运行的筛选结果可复现性。

### 项目结构

`crawler.py` 是保持兼容的入口文件，实际实现位于 `weibo_crawler/`：

- `config.py`：读取并校验 YAML 配置。
- `models.py`：定义配置和记录的数据模型。
- `browser.py`：管理持久化浏览器配置和登录流程。
- `parsers.py`、`selectors.py`：页面提取逻辑和微博选择器。
- `crawler.py`：协调采集队列和断点续爬。
- `database.py`：SQLite 去重、检查和导出。
- `filters.py`：用户筛选规则。
- `exporter.py`：CSV 和报告导出。
- `merge.py`：多设备数据库合并。
- `tests/`：解析、筛选、数据库和检查点单元测试。

## 环境要求

- Python 3.11 或更高版本。
- [uv](https://docs.astral.sh/uv/)。
- Playwright Chromium 浏览器及其系统依赖。

macOS、Debian 等 Linux 服务器均可运行。项目使用 `uv` 管理依赖和运行命令。

## 安装与准备

在项目根目录执行：

```bash
uv sync
uv run playwright install chromium
```

复制配置模板：

```bash
cp config.example.yaml config.yaml
```

然后编辑 `config.yaml`，填写母帖地址、账户配置和输出路径。`config.yaml` 已被 `.gitignore` 忽略，不会提交到版本库。

## 登录

登录需要在真实浏览器中手动完成。程序会为每个账户打开一个独立的持久化浏览器配置目录，登录状态会保留，后续爬取可以直接复用。

登录全部账户：

```bash
uv run python crawler.py login --config config.yaml --account all
```

只登录某一个账户：

```bash
uv run python crawler.py login --config config.yaml --account account_a
```

执行后浏览器会打开微博登录页，请手动完成登录，然后回到终端按 Enter。按 Enter 后该账户的登录会话会保存到对应的 `profile_dir`。

也可以使用项目安装的入口命令：

```bash
uv run weibo-crawler login --config config.yaml --account all
```

## 爬取

登录完成后运行：

```bash
uv run python crawler.py crawl --config config.yaml
```

如果需要更详细的调试日志：

```bash
uv run python crawler.py crawl --config config.yaml --verbose
```

爬取流程大致如下：

1. 读取配置，打开对应账户的持久化浏览器上下文。
2. 逐个母帖翻页，抽取评论用户并保存为待处理用户。
3. 逐个待处理用户抓取公开资料，判断是否满足筛选条件。
4. 逐个合格用户抓取其公开微博，直到微博内容数达到 `target_content_count` 或所有合格用户处理完毕。
5. 输出 CSV 和运行报告。

程序会随时把检查点和去重后的数据写入 `state_db`。中断后重新运行同样的命令，会从上次进度继续，不会重复入库已存在的用户或微博。

### 遇到频控或验证码

如果页面出现登录页、验证码、安全验证、访问频繁、账号异常等提示，程序会停止当前账户的抓取，保存状态，并提示：

```text
输入下一个账户编号继续，输入 q 停止：
```

你可以输入另一个已经登录的账户在 `accounts` 列表中的序号继续；直接按 Enter 会尝试下一个账户；输入 `q` 停止。

程序不会尝试绕过验证码或频控。

## 导出

只导出当前 `state_db` 中已经筛选成功的用户和已采集的微博：

```bash
uv run python crawler.py export --config config.yaml
```

默认输出：

- 用户基本信息：`data/users_basic.csv`
  - 字段：`用户ID, 性别, 出生日期, 教育信息, 注册时间, 微博总数, 粉丝数量`
- 微博内容：`data/posts_content.csv`
  - 字段：`用户ID, 正文, 位置, 日期, 是否原创`
- 每次运行的报告：`data/reports/run-*.json`

CSV 使用 UTF-8 BOM 编码，方便 Excel 直接打开。

## 多设备并行与数据库合并

多设备并行的关键是：

- 每个设备使用不同的 `device_id`；
- 每个设备使用不同的 `state_db`；
- 每个设备使用互不重叠的 `parent_posts`；
- 最终用 `merge-db` 汇总。

例如 macOS 和 Linux 服务器分别配置：

macOS 的 `config.yaml`：

```yaml
device_id: mac-01
state_db: data/state-mac-01.sqlite3
parent_posts:
  - id: "parent-001"
    url: "https://weibo.com/...第一条母帖..."
```

Linux 的 `config.yaml`：

```yaml
device_id: linux-01
state_db: data/state-linux-01.sqlite3
parent_posts:
  - id: "parent-002"
    url: "https://weibo.com/...第二条母帖..."
```

两台设备分别完成爬取后，把 SQLite 文件汇总到一处，执行：

```bash
uv run python crawler.py merge-db \
  --output data/merged.sqlite3 \
  data/state-mac-01.sqlite3 \
  data/state-linux-01.sqlite3
```

`merge-db` 会把 `commenters`、`users`、`posts`、`parent_posts` 合并去重。合并后的数据库适合统一导出和分析，不建议作为继续断点爬取的工作库，因为用户微博的细粒度检查点不会完整复制。

## Debian SSH 登录、无图形界面时如何运行

在没有桌面环境的 Debian 服务器上，Playwright Chromium 需要安装系统依赖：

```bash
sudo apt update
sudo apt install -y curl git
uv sync
uv run playwright install --with-deps chromium
```

`--with-deps` 会尝试安装 Chromium 所需的系统库，通常需要 `sudo` 权限。若服务器没有 `sudo`，请联系管理员安装依赖，或使用其他方式提供 Chromium 依赖。

### 登录态的准备

由于无图形界面无法看到并操作浏览器窗口，推荐先在本地 macOS 或带桌面的电脑上完成登录，再把已登录的浏览器配置目录复制到服务器。

例如本地账户目录为：

```text
.playwright/account-a
```

复制到服务器：

```bash
rsync -av --delete \
  .playwright/account-a \
  user@debian-server:/path/to/weibocraw/.playwright/account-a
```

服务器上的 `config.yaml` 中，对应账户仍指向该目录：

```yaml
accounts:
  - name: account_a
    profile_dir: .playwright/account-a
```

如果你的电脑上有 X Server，也可以在 SSH 时转发图形界面，然后手动登录：

```bash
ssh -X user@debian-server
cd /path/to/weibocraw
uv run python crawler.py login --config config.yaml --account account_a
```

登录完成后，即使断开图形转发，登录状态也已经保存在配置目录中。

### 无图形界面下的配置

登录态准备好后，把服务器配置中的 `crawler.headless` 设为 `true`：

```yaml
crawler:
  headless: true
```

然后执行爬取：

```bash
cd /path/to/weibocraw
uv run python crawler.py crawl --config config.yaml
```

注意：`headless: true` 适合已经登录的持久化配置目录。不要在没有图形界面、也没有已登录配置时直接运行 `login` 并期望手动登录；这样你看不到浏览器窗口。

### 保持长时间运行

SSH 断开后，前台进程可能被终止。建议使用 `tmux`、`screen` 或 `nohup`：

使用 `tmux`：

```bash
sudo apt install -y tmux
tmux new -s weibo
cd /path/to/weibocraw
uv run python crawler.py crawl --config config.yaml
```

按 `Ctrl-b` 后按 `d` 可脱离会话，之后可以安全断开 SSH。下次登录后重新进入：

```bash
tmux attach -t weibo
```

使用 `nohup`：

```bash
cd /path/to/weibocraw
nohup uv run python crawler.py crawl --config config.yaml \
  > data/crawl.log 2>&1 &
```

查看日志：

```bash
tail -f data/crawl.log
```

无论使用哪种方式，中断或断开后只要 SQLite 文件和浏览器登录配置没有损坏，重新运行 `crawl` 即可从检查点继续。

## 配置文件详解

配置使用 YAML 格式。推荐以 `config.example.yaml` 为模板，复制为 `config.yaml` 后修改。

### 完整示例

```yaml
device_id: mac-01
target_content_count: 12000
state_db: data/state-mac-01.sqlite3
eligibility_as_of: "2026-10-01"
max_posts_count: 20000

output:
  users_basic_csv: data/users_basic.csv
  posts_content_csv: data/posts_content.csv
  report_dir: data/reports

parent_posts:
  - id: "parent-001"
    url: "https://weibo.com/替换为第一条母帖地址"
  - id: "parent-002"
    url: "https://weibo.com/替换为第二条母帖地址"

accounts:
  - name: account_a
    profile_dir: .playwright/account-a
  - name: account_b
    profile_dir: .playwright/account-b

crawler:
  min_delay_seconds: 2.0
  max_delay_seconds: 5.0
  page_timeout_ms: 30000
  max_retries: 3
  max_comment_pages: 0
  max_post_pages_per_user: 0
  headless: false
```

### 顶层配置项

#### `device_id`

设备唯一标识，例如 `mac-01`、`linux-01`。主要用于区分不同设备和多设备协作时的配置与数据库命名，不能为空。

```yaml
device_id: mac-01
```

#### `target_content_count`

希望采集的合格用户微博内容总条数。当去重后的 `posts` 表数量达到该值后，爬取停止。默认 `12000`，必须至少为 `1`。

```yaml
target_content_count: 12000
```

#### `state_db`

SQLite 状态数据库路径。程序在这里保存母帖检查点、评论用户、用户资料、微博内容和账户事件。相对路径相对于配置文件所在目录解析。

```yaml
state_db: data/state-mac-01.sqlite3
```

#### `eligibility_as_of`

判断“注册满一年”所使用的基准日期，默认 `2026-10-01`。使用固定日期可以让多次运行的筛选结果保持一致。

```yaml
eligibility_as_of: "2026-10-01"
```

#### `max_posts_count`

用户发帖总数的上限。发帖总数大于该值的用户会被判定为不合格并舍弃。省略或设为 `0` 表示不启用该上限。

```yaml
max_posts_count: 20000
```

#### `output`

输出文件配置：

```yaml
output:
  users_basic_csv: data/users_basic.csv
  posts_content_csv: data/posts_content.csv
  report_dir: data/reports
```

- `users_basic_csv`：合格用户基本信息导出路径。
- `posts_content_csv`：微博内容导出路径。
- `report_dir`：运行报告目录。

#### `parent_posts`

母帖列表，即你要抽样的微博评论区。每个母帖需要一个唯一 `id` 和一个 `url`。

```yaml
parent_posts:
  - id: "parent-001"
    url: "https://weibo.com/替换为母帖地址"
  - id: "parent-002"
    url: "https://weibo.com/替换为另一条母帖地址"
```

要求：

- `id` 在同一配置内必须唯一；
- `url` 不能为空；
- 建议直接使用评论页或正文页 URL，保证已登录账户能正常看到评论。

配置解析器也兼容字符串列表的旧写法，但不推荐，因为缺少稳定的 `id` 会影响检查点语义：

```yaml
parent_posts:
  - "https://weibo.com/...第一条..."
```

#### `accounts`

已登录微博账户列表。每个账户必须指定 `name` 和 `profile_dir`。

```yaml
accounts:
  - name: account_a
    profile_dir: .playwright/account-a
  - name: account_b
    profile_dir: .playwright/account-b
```

- `name`：账户标识，必须唯一。
- `profile_dir`：该账户的 Playwright 持久化浏览器配置目录，用于保存登录状态。相对路径相对于配置文件所在目录解析。每个账户应使用不同目录，避免登录状态互相覆盖。

登录时使用 `--account all` 会按顺序处理所有账户；`--account account_a` 只处理指定账户。

#### 旧版兼容配置

旧配置中常见的顶层字段仍会被读取，以兼容旧脚本和测试，但新配置建议使用上面的结构化写法：

```yaml
target_count: 12000
output_csv: data/weibo_posts.csv
profile_dir: .playwright/weibo-profile
report_dir: data/reports
min_delay_seconds: 2.0
max_delay_seconds: 5.0
page_timeout_ms: 30000
headless: false
keywords: []
start_date: ""
end_date: ""
```

其中 `keywords`、`start_date`、`end_date` 目前仅作为旧字段兼容保留，新的爬取流程不会把它们用作实际过滤条件。如果你需要限定采集日期或关键词，请不要依赖这些字段。

### `crawler` 配置项

```yaml
crawler:
  min_delay_seconds: 2.0
  max_delay_seconds: 5.0
  page_timeout_ms: 30000
  max_retries: 3
  max_comment_pages: 0
  max_post_pages_per_user: 0
  headless: false
```

#### `min_delay_seconds`、`max_delay_seconds`

两次翻页或请求之间的随机等待时间范围，单位为秒。程序会在该区间内随机等待，降低访问频率。

```yaml
min_delay_seconds: 2.0
max_delay_seconds: 5.0
```

必须满足：

```text
0 <= min_delay_seconds <= max_delay_seconds
```

#### `page_timeout_ms`

页面加载和元素操作的默认超时时间，单位为毫秒。默认 `30000`。

```yaml
page_timeout_ms: 30000
```

#### `max_retries`

预留的重试次数配置项。当前代码加载了该值，但核心采集流程暂未显式按该值自动重试。可以保留在配置中，但不要依赖它改变当前行为。

```yaml
max_retries: 3
```

#### `max_comment_pages`

每个母帖最多翻页采集评论的页数。设为 `0` 表示不设上限，一直翻到没有下一页为止。

```yaml
max_comment_pages: 0
```

注意：一旦设置了大于 `0` 的值，达到该页数后该母帖会被标记为已完成，后续运行不会继续翻剩余评论页。因此如果你希望完整抽样某个母帖，请保持 `0`。

#### `max_post_pages_per_user`

每次运行时，每个合格用户最多采集的微博页数。设为 `0` 表示不设上限。这个上限按单次运行生效，中断或下次运行时仍可从该用户的检查点继续。

```yaml
max_post_pages_per_user: 0
```

#### `headless`

是否使用无头浏览器模式。

```yaml
headless: false
```

- 本地 macOS 登录和调试时建议 `false`。
- 无图形界面的 Debian 服务器上，使用已经登录好的配置目录时，建议设为 `true`。
- 不要在有图形界面的 `login` 流程中设为 `true`，否则看不到登录窗口。

## 命令行参考

所有命令都在项目根目录运行，使用 `uv run` 保证依赖环境一致。

### 登录

```bash
uv run python crawler.py login --config config.yaml --account all
uv run python crawler.py login --config config.yaml --account account_a
```

### 爬取

```bash
uv run python crawler.py crawl --config config.yaml
uv run python crawler.py crawl --config config.yaml --verbose
```

`--verbose` 会输出 DEBUG 级日志。

### 导出

```bash
uv run python crawler.py export --config config.yaml
```

### 合并数据库

```bash
uv run python crawler.py merge-db \
  --output data/merged.sqlite3 \
  data/state-a.sqlite3 \
  data/state-b.sqlite3
```

### 单元测试与语法检查

```bash
uv run python -m unittest discover -s tests -v
python3 -m py_compile crawler.py
```

也可以使用项目脚本入口：

```bash
uv run weibo-crawler login --config config.yaml --account all
uv run weibo-crawler crawl --config config.yaml
uv run weibo-crawler export --config config.yaml
uv run weibo-crawler merge-db --output data/merged.sqlite3 data/state-a.sqlite3
```

## 数据库与断点机制

`state_db` 是 SQLite 数据库，主要表如下：

- `parent_posts`：母帖及其完成状态和当前页数。
- `commenters`：母帖与评论用户的对应关系，主键 `(parent_id, user_id)`。
- `users`：评论用户及其资料，主键 `user_id`。
- `posts`：采集到的微博内容，主键 `post_id`。
- `checkpoints`：母帖和用户微博翻页检查点。
- `account_events`：账户被标记为受限等事件记录。

去重规则：

- 同一个母帖下的同一个评论用户只保存一次；
- 同一个用户只保存一条用户资料，后采集的非空字段会补全空白字段；
- 同一个微博 `post_id` 只保存一次。

母帖翻页、用户微博翻页都会写入检查点。爬取中断后重新运行，程序会从检查点继续，不会从零开始。

## 常见问题

### 启动时提示 Playwright 未安装

执行：

```bash
uv sync
uv run playwright install chromium
```

Debian 无图形环境执行：

```bash
uv run playwright install --with-deps chromium
```

### 提示页面要求登录或验证

这是程序的保护机制。它不会绕过验证码或登录，而会保存进度并提示切换账户。请确认对应账户已登录且没有被微博风控，或者换用其他已登录账户。

### `no_eligible_users`

说明发现并成功解析了用户，但没有用户满足筛选条件。可能原因包括：

- 评论用户注册未满一年；
- 微博总数少于 50；
- 微博总数超过 `max_posts_count`（如果启用）；
- 粉丝数超过 5000；
- 页面结构变化导致注册时间、微博数或粉丝数没有解析成功。

检查运行报告和日志，确认用户资料字段是否被正确提取。

### `page_timeout`

页面加载超时。可适当调大 `crawler.page_timeout_ms`，或检查网络、代理和微博访问速度。

### SSH 断开后任务停止

使用 `tmux`、`screen` 或 `nohup` 让爬取进程在后台继续。数据库检查点已持久化，重新运行 `crawl` 也能续爬。

### 合并后的数据库还能继续爬取吗

建议不要。`merge-db` 主要用于汇总各设备数据库并统一导出，不会完整合并细粒度检查点。各设备的原始 `state_db` 才是各自继续断点爬取的工作库。

## 安全与合规

- 只采集登录账户在正常浏览状态下可见的公开字段。
- 不绕过登录、验证码、访问控制、隐私设置或频率限制。
- 遇到验证码、登录页或频控时立即停止当前账户并保存状态。
- 保持保守的抓取频率，遵守微博服务条款和适用法律法规。
