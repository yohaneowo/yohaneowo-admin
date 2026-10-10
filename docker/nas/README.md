# 在群晖 NAS 上部署 yohaneowo-admin

镜像在电脑上构建、推到 Docker Hub，NAS 只负责拉镜像来跑，跟 yohaneowo-bot 的做法一样。

| 镜像 | 内容 |
| --- | --- |
| `yohane0w0/yohaneowo-admin-backend` | FastAPI 后端（`docker/backend/Dockerfile`） |
| `yohane0w0/yohaneowo-admin-web` | Nginx + 构建好的前端（`docker/nas/web.Dockerfile`） |
| `yohane0w0/yohaneowo-admin-backup` | 数据库备份服务：每日自动导出、手动备份、还原、维护只读账号（`docker/nas/backup/`） |

## 发布新版本（电脑上）

```bash
docker login            # 第一次需要
docker/nas/publish.sh   # 默认构建 master，也可指定：docker/nas/publish.sh <分支或提交>
```

脚本从**已提交**的代码构建（先检出到临时目录），工作目录里没提交的改动不会被打包。
每次都会推 `latest` 和 git 版本号两个 tag。只想构建不推送：`NO_PUSH=1 docker/nas/publish.sh`。

## 第一次部署（NAS 上）

1. 在 NAS 上建一个文件夹，例如 `/volume1/docker/yohaneowo-admin`。
2. 把 `compose.yaml` 放进去，再照 `.env.example` 建一个 `.env`，所有密码和密钥都要填，用随机值。
3. 在该文件夹执行 `docker compose up -d`，或在 Container Manager 里用 `compose.yaml` 建项目。
4. 打开 `http://<NAS 的 IP>:18090/web`（端口是 `.env` 的 `WEB_PORT`）。
5. **立刻改掉默认密码**：`super`、`admin`、`user` 三个账号的初始密码都是 `123456`。
   用不到的账号建议直接停用。

第一次启动时，后端会自动建表并导入菜单、角色等初始数据，Bot管理的菜单也包含在内。

## 连接 yohaneowo-bot（稳定版）

admin 会建立一个名为 `yohaneowo-admin` 的 Docker 网络。bot 的 `compose.nas.yaml` 加入这个网络后，
就能通过 `http://yohaneowo-admin-web` 上报，不需要对外开端口。

- bot 的 `.env` 加上 `ADMIN_API_TOKEN=<跟 admin 的 BOT_API_TOKEN 同一个值>`。
- **要先启动 admin，再启动 bot**，否则 bot 找不到这个网络，启动会失败。
- 测试版 bot（`compose.nas.dev.yaml`）不要接到这里：两个 bot 上报到同一个 admin，群组同步会互相覆盖。

## 更新

```bash
docker compose pull && docker compose up -d
```

数据库结构有变化时，后端启动时会自动执行迁移。更新前建议先手动备份一次（见下）。

## 数据与备份

| 位置 | 内容 |
| --- | --- |
| volume `yohaneowo-admin_mysql` | MySQL 数据：群组、日志、资产记录、账号…… |
| volume `yohaneowo-admin_redis` | Redis 数据（缓存、登录状态；丢了只需重新登录） |
| volume `yohaneowo-admin_upload` | 后台上传的文件 |
| `./backup/` | 数据库备份（`fastapiadmin-<时间>.sql.gz`） |

数据都放在 Docker volume，不绑定 NAS 文件夹：群晖文件夹的 ACL 不让容器里的非 root 用户写入
（Redis 报 `appendonlydir: Permission denied`、MySQL 报 `data directory ... is unusable`），
而且数据库运行中直接复制数据文件，可能拷到写了一半的状态，还原时会损坏。

### 自动备份

`backup` 服务每天 `BACKUP_TIME`（默认 04:00）用 `mysqldump` 导出整个数据库到 `./backup/`，
保留 `BACKUP_KEEP_DAYS`（默认 30）天。导出时不锁表，后端照常运作。

用 **Hyper Backup 备份 `./backup/` 文件夹**，备份到外接硬盘或云端，就有一份异地备份。

### 手动备份

改数据库、更新版本之前先备份一次：

```bash
sudo docker compose exec backup /backup.sh
```

或在 Container Manager：容器 → `yohaneowo-admin-backup-1` → 终端机 → 执行 `/backup.sh`。

### 还原

会覆盖整个数据库，先手动备份一次现在的数据：

```bash
sudo docker compose exec backup /restore.sh            # 列出可用的备份
sudo docker compose stop backend web                   # 还原期间停掉后端
sudo docker compose exec backup /restore.sh fastapiadmin-20261010-040000.sql.gz
sudo docker compose start backend web
```

建议偶尔拿备份还原到电脑上的开发环境试一次，确认备份真的能用。

## 查看与修改数据库

### 用 DBeaver 经 SSH 隧道连接

MySQL 只开在 NAS 本机的 `127.0.0.1:13306`（`MYSQL_TUNNEL_PORT`），局域网连不进来，
要先 SSH 登录 NAS 再转过去。DSM 要先开启 SSH（控制面板 → 终端机和 SNMP）。

DBeaver 新建 MySQL 连接：

| 页签 | 设置 |
| --- | --- |
| 主要 | 主机 `127.0.0.1`、端口 `13306`、数据库 `fastapiadmin` |
| 主要 | 用户 `readonly`、密码是 `.env` 的 `MYSQL_READONLY_PASSWORD` |
| SSH | 勾选「使用 SSH 隧道」，主机填 NAS 的 IP、端口 22，用你的 DSM 账号登录 |

`readonly` 只能查看（SELECT），浏览时手滑也改不到数据。

### 修改数据

1. **能在后台改的就在后台改**：后台会检查数据格式，也会处理缓存。
2. 必须直接改时：**先手动备份**，再用 `fastapiadmin` 账号（密码是 `.env` 的 `MYSQL_PASSWORD`）连接。
   不确定的 SQL 先在电脑上的开发环境试。
3. 系统参数、字典这类数据有 Redis 缓存，直接改数据库后要到后台「监控管理 → 缓存」清掉，
   或重启后端。
4. **不要手动改表结构**（加栏位、改类型），结构变动要用代码里的迁移文件。

## 以后要从外面访问

目前设定只在局域网用（HTTP）。要从外面访问的话：

1. 在 Cloudflare Tunnel 加一个 Public Hostname，例如 `admin.yohaneowo.com` → `http://yohaneowo-admin-web:80`
   （cloudflared 容器也要加入 `yohaneowo-admin` 网络）。
2. 在 Cloudflare Zero Trust 加一层 **Access** 验证，只允许你自己的账号，不要让后台登录页直接暴露在公网。
3. `.env` 的 `ALLOWED_HOSTS` 改成实际网址，例如 `["admin.yohaneowo.com","yohaneowo-admin-web"]`。
