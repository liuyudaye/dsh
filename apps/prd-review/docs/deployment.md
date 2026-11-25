# 服务器部署

## 用 Docker Compose 启动

在 Linux 服务器准备 Docker 和 Compose，在同一个 DSH 仓库中检出要发布的标签或提交。以下命令从仓库根目录运行。先创建 `apps/prd-review/.env`，填好 `APP_PASSWORD` 和 `NEWAPI_API_KEY`。报告登录密码至少需要 16 位。使用飞书链接评审时，再填飞书应用配置。

```sh
test -f apps/prd-review/.env || install -m 600 apps/prd-review/.env.example apps/prd-review/.env
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:8000/healthz
```

Compose 构建当前检出的 DSH 源码和 PRD 应用，从同一个镜像启动 `reports` 和 `agent`。`prd-data` 保存两项服务共用的报告数据库，`dsh-data` 保存 DSH 对话、附件和工作区。服务器默认只在回环地址发布 `8000` 和 `3080`。个人访问可从自己的电脑建立 SSH 隧道。

从旧 Compose 项目升级时，先备份原报告卷。在新仓库启动前，停止原项目中占用 `8000` 和 `3080` 的服务。需要沿用原报告卷时，将每条命令里的 `docker compose` 替换为 `docker compose -p 原项目名`。

```sh
ssh -N -L 8000:127.0.0.1:8000 -L 3080:127.0.0.1:3080 user@server
```

报告工作台打开 `http://127.0.0.1:8000/`，用 `apps/prd-review/.env` 中的 `APP_USER` 和 `APP_PASSWORD` 登录。DSH 的带令牌登录链接从服务器的 `docker compose logs agent` 获取，令牌只发给有授权的人。

## 用 HTTPS 域名访问

给对话和报告各配一个域名。在服务器的 `apps/prd-review/.env` 设置实际对外地址，然后运行 `docker compose up -d` 应用配置。

```dotenv
DSH_PUBLIC_URL=https://chat.example.com/
PRD_REPORT_URL=https://reports.example.com
COOKIE_SECURE=1
```

DSH 启动器从 `DSH_PUBLIC_URL` 推导信任的 `Host`。以下 Nginx 片段放在 `http` 块内，证书路径和域名按服务器配置替换。代理保留浏览器的 `Host` 和 `Origin`，转发 WebSocket 升级，并为流式响应关闭缓冲。用 `nginx -t` 检查后重载，再使用新日志中的 HTTPS 登录链接。[DSH 反向代理说明](../../../docs/user/guide/public-deployments.zh.md) · [Nginx WebSocket 代理文档](https://nginx.org/en/docs/http/websocket.html)

```nginx
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

server {
    listen 443 ssl;
    server_name chat.example.com;
    ssl_certificate     /path/to/chat/fullchain.pem;
    ssl_certificate_key /path/to/chat/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:3080;
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
        proxy_set_header Origin $http_origin;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_cookie_flags ~ secure;
        proxy_buffering off;
        proxy_read_timeout 300s;
        access_log off;
    }
}

server {
    listen 443 ssl;
    server_name reports.example.com;
    ssl_certificate     /path/to/reports/fullchain.pem;
    ssl_certificate_key /path/to/reports/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $http_host;
        proxy_buffering off;
        proxy_read_timeout 300s;
    }
}
```

两个入口适合受信团队。DSH 登录者可以调用服务器上的 Agent 工具，公开给团队时应同时通过公司的访问控制限制谁能到达 HTTPS 入口。

## 备份和迁移已有报告

从当前仓库根目录备份本机的报告库。旧本机环境的数据通过 `apps/prd-review/data/` 链接访问。SQLite 备份 API 会为正在使用的数据库生成独立快照。备份文件含报告正文，应通过受控渠道传输和保存。[Python SQLite 备份](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup)

```sh
python3 - <<'PY'
import sqlite3
from contextlib import closing
from pathlib import Path

database = Path("apps/prd-review/data/reviews.sqlite3").resolve()
backup = database.with_name("reviews-backup.sqlite3")
with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as source:
    backup.touch(mode=0o600, exist_ok=False)
    with closing(sqlite3.connect(backup)) as destination:
        source.backup(destination)
print(backup)
PY
```

把快照传到新服务器。初次启动 Compose 之前按下面方式导入，命令会检查快照并只向空数据库位置写入。已有数据的目标卷需要先备份并单独安排合并或恢复。

```sh
docker compose build reports
docker compose run --rm --no-deps -T --entrypoint python reports -c '
import os, shutil, sqlite3, sys, tempfile
from contextlib import closing
from pathlib import Path

target = Path(os.environ["DATA_DIR"]) / "reviews.sqlite3"
with tempfile.NamedTemporaryFile(dir=target.parent) as staged:
    shutil.copyfileobj(sys.stdin.buffer, staged)
    staged.flush()
    uri = Path(staged.name).as_uri() + "?mode=ro&immutable=1"
    with closing(sqlite3.connect(uri, uri=True)) as snapshot:
        if snapshot.execute("PRAGMA quick_check").fetchone() != ("ok",):
            sys.exit("SQLite 备份校验失败")
        snapshot.execute("SELECT 1 FROM versions LIMIT 1").fetchall()
    os.link(staged.name, target)
' < /path/to/reviews-backup.sqlite3
docker compose up -d
```

DSH 对话、附件和工作区随 `dsh-data` 卷备份。备份整个卷前先停 `agent`，完成后再启动。

## 发布定制版本

在同一仓库维护 DSH 界面与 PRD 功能。上线时，服务器检出包含这些改动的发布标签并重新构建镜像。保持 Compose 项目名不变，服务会继续使用原来的持久化卷。

```sh
git fetch --tags
git switch --detach YOUR_RELEASE_TAG
docker compose up -d --build
docker compose ps
```

开发预览和测试入口见 [PRD 应用说明](../README.md)。
