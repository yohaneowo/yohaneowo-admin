# yohaneowo-admin

fork 自 [FastapiAdmin](https://github.com/fastapiadmin/FastapiAdmin)（远端 `upstream`），当作 **yohaneowo-bot**（`../yohaneowo-bot`，Node.js 的 Discord / LINE bot）的管理后台。后端 FastAPI + SQLAlchemy + Alembic（`backend/`），前端 Vue 3 + Element Plus（`frontend/web/`），MySQL 8 + Redis 7。

自己加的部分都在插件里，框架本身尽量不改，方便之后合并 upstream：

| 位置 | 内容 |
|---|---|
| `backend/app/plugin/module_bot/` | Bot管理：群组列表、解析日志、运行日志、资产记录、语音记录；`internal_auth.py`（bot 的 token 验证）、`log_common.py`、`seed_menu.py` |
| `backend/app/plugin/module_ai_usage/` | AI 调用日志、费用排行、模型价格、费用对账（菜单挂在框架自带的「AI管理」底下）。任何项目都能上报（第一版共用 `BOT_API_TOKEN`）；单价每天从 LiteLLM 公开价格表同步（`pricing.py`），手动改过的会锁定；对账用 `ANTHROPIC_ADMIN_API_KEY` 拿 Anthropic 实际帐单比对（`anthropic_admin.py`）。`gateway/` 是 AI 转发：bot 把 Claude / Voyage 的请求体交给 `/internal/ai/messages`、`/internal/ai/embeddings`，admin 用 `ANTHROPIC_API_KEY`、`VOYAGE_API_KEY` 调用并直接写调用日志（bot 不再持有这两个 key）。NAS 的 compose 只传明确列出的环境变量，新增设定要记得加进 `docker/nas/compose.yaml` |
| `backend/app/plugin/module_external/` | 第三方接口（菜单挂在框架的「接口管理」底下）：TMDB、Trakt、OMDb、Wikipedia 每天的请求 / 命中快取 / 失败，以及每日上限、重置时区（页面上改，不写死）。外部请求都先 `service.remaining()` 再 `service.record()`；对方拒绝（额度用完）时 `service.block()` 当天停止 |
| `backend/app/plugin/module_bot/movie/sync/` | 观影统计同步（从 bot 的 JS 移植，栏位与顺序跟原版一致）：Trakt 纪录 → 口味 → 网站 snapshot + bot 用的 `taste`。电影路由的依赖 `job.ensure_loop` 在第一次被调用时启动背景循环；多个 uvicorn worker 各跑一个循环，靠领取锁定不会重复同步。外部调用每次开短交易，别把整个同步包在一个交易里（会锁住用量表） |
| `backend/app/plugin/module_bot/movie/sources/` | 外部来源客户端（tmdb / trakt / omdb / wikipedia），key 在 Doppler。bot 经 `/internal/movies/sources/*` 代查，路径有白名单 |
| `backend/app/plugin/module_project/` | 项目依赖：各项目上报的依赖清单，检查上游维护状态、版本、已知漏洞 |
| `frontend/web/src/api/module_bot/`、`src/views/module_bot/` | 对应的前端 API 与页面（`shared.ts` 放共用的选项和 hook） |
| `docker/nas/` | NAS 部署：镜像发布脚本、compose、备份服务 |

## 工作约定

- 用中文回复。commit 讯息照现有格式：`feat(bot): …`、`fix(deploy): …`，中文摘要，结尾加 `Co-Authored-By`。只有被要求时才 commit / push。
- 密钥在 Doppler（项目 `yohaneowo-admin`：`dev` = 本机，`prd` = NAS），不再用 `backend/env/.env.*`。不要印出值（`doppler secrets` 会印出，用 `--only-names`）。新电脑先 `doppler login`，再在本 repo 根目录 `doppler setup`。NAS 上 MySQL / Redis 密码仍在 `docker/nas/.env`（官方镜像读不到 Doppler）。Git Bash 里执行 `doppler` 要加 `env -u PWD`（它会读 Unix 格式的 `PWD`，找不到登录状态）。
- 后端改完跑 `uv run ruff check` 和 `uv run ruff format`（以现有文件的格式为准）。前端改完跑 `npx eslint`、`npx prettier --write`（只针对改过的文件）和 `npx vue-tsc --noEmit`。

## 时间与时区

- 数据库一律存 **UTC**（模型默认值用 `datetime.now(UTC)`）。页面显示与前端送来的时间是 `DISPLAY_TIMEZONE`（默认 `Asia/Taipei`）的本地时间，换算集中在 `app/core/timezone.py`。
- 回应里的时间栏位一律用 `DateTimeStr`（输出自动换成本地时间；前端送来的字串自动换成 UTC），不要用裸 `datetime` 输出。bot 上报用的内部 schema 才用 `datetime`（bot 送的是带时区的 ISO 字串）。
- 按日统计用 `local_date(column)`，「今天零点」用 `local_today_start_utc()`，不要用 `date.today()` 或 `func.date()`。
- 前端解析后端时间字串时**不要**再加 `Z`（后端送的已经是本地时间）。
- 例外：AI管理 → 费用对账的日期刻意用 UTC，跟供应商帐单的分日一致，页面上有标注。

## 本机开发

- MySQL、Redis 在 Docker：`yohaneowo-admin-mysql`（`127.0.0.1:13306`，库名 `fastapiadmin`）、`yohaneowo-admin-redis`（`127.0.0.1:6379`）。
- 后端：`cd backend && doppler run -p yohaneowo-admin -c dev -- uv run --extra mysql main.py run --env=dev`（端口 8001，dev 会自动 reload）。其他要读设定的指令（`main.py revision/upgrade`、`seed_menu`）也要加 `doppler run -p yohaneowo-admin -c dev --`。路径要写对大小写：Doppler 比对 `doppler setup` 绑定的目录时区分大小写，所以指令都明确写 `-p` / `-c`。用户通常已经开着一个；**不要关掉它**，需要测新路由时另开一个临时实例在别的端口（`uv run uvicorn app:create_app --factory --port 8011`），测完关掉。
- 前端：`cd frontend/web && pnpm dev`。
- 在 Windows 终端执行会输出中文的指令，前面加 `PYTHONIOENCODING=utf-8`，不然会因 cp950 编码报错。

## 与 bot 对接

- bot 端全部在 bot repo 的 `services/adminApi.js`。admin 端每个模块的 `controller.py` 有两组路由：后台页面用的 `...Router`（要登录与权限），以及给 bot 用的 `...InternalRouter`（前缀 `/internal/...`，依赖 `verify_bot_token`，请求头 `X-Bot-Token` = `BOT_API_TOKEN`）。
- 上报 schema 的 `max_length` 要跟 bot 端的截断一致；批量上报上限 `MAX_LOG_BATCH`（`log_common.py`）对应 bot 的 `LOG_BATCH_SIZE`。改了任一边都要同步另一边。
- 环境分开：测试 bot → 电脑上的这个 admin；稳定 bot → NAS 上的 admin（Docker 网络 `yohaneowo-admin`，bot 用 `http://yohaneowo-admin-web` 连进来）。**不要让两个 bot 上报到同一个 admin**，群组同步会互相覆盖。

### 新增一种 bot 上报的资料

1. 在 `module_bot/<名称>/` 照 `parse_log` 的结构写 model / schema / crud / service / controller。路由由 `app/core/discover.py` 自动发现，不用另外注册。
2. 迁移：先确认 `uv run alembic -c alembic.ini current` 已经是 head（否则自动生成会混进别的改动），再 `uv run main.py revision --env=dev -m "说明"`，检查生成的文件只包含自己的表，然后 `uv run main.py upgrade --env=dev`。
3. 菜单：在 `backend/sql/sys_menu.json` 对应的顶层菜单（Bot管理、项目依赖、AI管理）下加页面和按钮（权限码 `module_bot:<名称>:query` / `:detail`），再跑 `uv run python -m app.plugin.module_bot.seed_menu --env=dev` 补进已经初始化过的数据库。全新的数据库启动时会自动导入。
4. 前端：`src/api/module_bot/<名称>.ts` 和 `src/views/module_bot/<名称>/index.vue`，照现有页面的写法（`useTable`、`FaSearchBar`、`useAutoRefresh`）。
5. bot 端在 `services/adminApi.js` 加上报，两边一起测，测完删掉测试资料。

## 多个对话同时工作

用户可能同时开着这个 repo 和 bot repo 的 Claude Code 对话。需要对接的功能在同一个对话里把两边一起做完（另一个 repo 用 `/add-dir` 加进来）。同一时间只让一个对话改同一个 repo：开始前看 `git status`，有不是自己做的未提交改动就不要覆盖，也不要混进自己的 commit。

## 部署（NAS）

`docker/nas/README.md` 有完整步骤。镜像用 `docker/nas/publish.sh` 从**已提交**的代码构建并推到 Docker Hub，NAS 只拉镜像。网页在 `http://<NAS>:18090/web`。要先启动 admin，再启动稳定版 bot，bot 才找得到 `yohaneowo-admin` 网络。
