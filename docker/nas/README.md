# 在群晖 NAS 上部署 yohaneowo-admin

镜像在电脑上构建、推到 Docker Hub，NAS 只负责拉镜像来跑，跟 yohaneowo-bot 的做法一样。

| 镜像 | 内容 |
| --- | --- |
| `yohane0w0/yohaneowo-admin-backend` | FastAPI 后端（`docker/backend/Dockerfile`） |
| `yohane0w0/yohaneowo-admin-web` | Nginx + 构建好的前端（`docker/nas/web.Dockerfile`） |

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
4. 打开 `http://<NAS 的 IP>:8090/web`（端口是 `.env` 的 `WEB_PORT`）。
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

数据库结构有变化时，后端启动时会自动执行迁移。

## 数据与备份

| 位置 | 内容 |
| --- | --- |
| `./mysql/` | MySQL 数据：群组、日志、资产记录、账号…… |
| `./redis/` | Redis 数据（缓存、登录状态；丢了只需重新登录） |
| volume `yohaneowo-admin_upload` | 后台上传的文件 |

用 Hyper Backup 备份 `./mysql/` 即可。

## 以后要从外面访问

目前设定只在局域网用（HTTP）。要从外面访问的话：

1. 在 Cloudflare Tunnel 加一个 Public Hostname，例如 `admin.yohaneowo.com` → `http://yohaneowo-admin-web:80`
   （cloudflared 容器也要加入 `yohaneowo-admin` 网络）。
2. 在 Cloudflare Zero Trust 加一层 **Access** 验证，只允许你自己的账号，不要让后台登录页直接暴露在公网。
3. `.env` 的 `ALLOWED_HOSTS` 改成实际网址，例如 `["admin.yohaneowo.com","yohaneowo-admin-web"]`。
