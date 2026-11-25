# PRD 评审

在 DSH 对话里评审产品文档，再到报告工作台编辑模块和流程画布，查看文字报告、测试用例和原文对比。

## 本地开发

用编辑器打开整个 DSH 仓库。从仓库根目录执行以下命令。开发环境需要 Node.js 24.9+、Corepack、Python 3.12+、uv 和 DSH 的本机构建工具。[DSH 源码构建指南](../../docs/development.zh.md)

```sh
corepack pnpm install --frozen-lockfile
DSH_GATE_CONCURRENCY=1 corepack pnpm run build:artifacts
corepack pnpm run prd:setup
test -f apps/prd-review/.env || install -m 600 apps/prd-review/.env.example apps/prd-review/.env
```

在 `apps/prd-review/.env` 设置 `NEWAPI_API_KEY` 和至少 16 位的 `APP_PASSWORD`。启动对话台和报告工作台。

```sh
corepack pnpm run prd:start --open
```

- DSH 对话台使用 `3080`，可从自动打开的浏览器标签或终端的登录链接进入。
- 报告工作台使用 `8000`，可用 `.env` 中的账号和密码登录。默认用户名为 `prd`。
- 在对话里粘贴正文、上传 UTF-8 的 Markdown 或 TXT，或指定 DSH 工作区内的文件，再让 Agent 评审并保存到报告工作台。产品更新后，可以让 Agent 沿用原文档的版本历史。
- 报告有测试用例时，点「下载测试用例（XMind）」并用 XMind 打开 `.xmind` 文件。也可以在 DSH 对话里获取已保存版本的下载链接。

## 编辑报告画布

打开已归档的报告即可进入画布。用模块导航定位流程，选中节点或连线编辑内容，拖动节点移动卡片，从连接点拖到目标节点建立分支。提交前可撤销和重做本次改动。保存会新增画布版本，恢复历史版本也会留下记录。画布提供文字报告、原文对照和用例下载入口。

画布使用 `frontend/` 下的 React 和 Vite 项目。修改前端后，可从仓库根目录运行 `corepack pnpm run prd:canvas:build`，由工作台提供构建产物。启动工作台后，运行 `npm run --prefix apps/prd-review/frontend dev` 可以监听前端源码并重新构建。

在对话台的**设置 → 模型**添加或编辑提供商。`.env` 中的 NewAPI 路由为 Web profile 提供一次初始配置。之后网页保存的提供商和默认模型随该 profile 一起保留。报告工作台的独立评审继续使用其 `NEWAPI_*` 环境变量。

改 DSH 聊天界面时，可以另开 `3081` 预览。它监听前端源码，使用已经运行的 `8000` 报告服务。

```sh
corepack pnpm run prd:dev --open
```

## 代码位置

```text
packages/client/                 DSH 前端组件
apps/prd-review/
  frontend/                      React 画布、编辑交互和前端构建
  src/prd_review/agent/          PRD MCP 服务、评审 Skill 和启动器
  src/prd_review/web/            报告页面、画布 API 和版本对比
  src/prd_review/integrations/   飞书只读客户端和 NewAPI HTTP 客户端
  assets/                        报告页和飞书 CLI 的 Node 依赖
  tests/                         PRD 业务回归测试
```

`apps/prd-review/data/` 保存本地报告、会话和工作区。合并前的本机环境通过本地链接沿用原来的 `.env` 和数据目录，新检出按上面的步骤配置。Git 忽略运行数据和凭证。

## 在 DSH 中调用 PRD 工具

在对话中说明「用 `prd-review` Skill 评审下面的需求，保存到报告工作台，并给我 XMind 链接」，随后粘贴正文。报告工作台的表单也可单独发起评审。

启动器向 DSH profile 挂载 `dsh-skill-filesystem` 和 `dsh-mcp-client`，分别读取应用内的 Skill 目录和通过 stdio 连接 Python MCP 服务。模型从 Skill 目录发现流程，从 MCP 工具列表发现可调用的能力。启动器只向 MCP 子进程传入业务所需的配置，聊天模型和报告登录的配置由各自服务读取。

MCP 服务使用官方 Python SDK 处理协议。已知工具的参数先按其 JSON Schema 校验，校验失败会指出字段供模型修正。业务失败返回 `isError`，未知工具返回 JSON-RPC 错误。批次保存后可在同一文档草稿上续做。

## 飞书和测试

读取飞书链接需要 `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET`。为应用启用机器人能力和 `docx:document:readonly`。读取知识库页面还需要 `wiki:node:read`。在飞书文档中给应用授予查看权限。`prd:setup` 会安装飞书 CLI。

从仓库根目录运行 PRD 业务回归测试。

```sh
corepack pnpm run prd:test
```

## 服务器部署

Compose 使用这个仓库的 DSH 源码和 PRD 应用构建镜像。发布时，在服务器检出自己的发布标签，再执行部署命令。[服务器部署、HTTPS 和备份迁移](docs/deployment.md)
