"""Launch this DSH source checkout with the NewAPI and PRD plugins."""

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

from dotenv import dotenv_values
from ruamel.yaml import YAML, YAMLError

from ..config import AGENT_PORT, BASE_URL, DSH_SOURCE_ROOT, MODEL, PROJECT_ROOT, REPORT_PORT


SOURCE_PREVIEW_PORT = 3081


def write_patch(directory, name, operations):
    path = directory / name
    content = json.dumps(operations, indent=2, ensure_ascii=False) + "\n"
    if not path.exists() or path.read_text(encoding="utf-8") != content:
        temporary = path.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    return path


def seed_web_model_config(home, defaults):
    """Seed the editable Web profile once, preserving existing settings."""
    profile = home / "profiles" / "web"
    marker = profile / ".prd-model-config-initialized"
    if marker.exists():
        return
    profile.mkdir(parents=True, exist_ok=True)
    path = profile / "cordis.patch.yml"
    lock = profile / "package.json.lock"
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise ValueError(f"{lock} 正在锁定配置。等待正在进行的保存完成后重试。") from error
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as owner:
            owner.write(f"{os.getpid()}\n")
        if marker.exists():
            return
        yaml = YAML(typ="rt")
        try:
            before = path.read_text(encoding="utf-8") if path.exists() else "[]\n"
            operations = yaml.load(before)
        except (YAMLError, UnicodeError) as error:
            raise ValueError(f"{path} 含有无效的 YAML。请修复后重试。") from error
        if not isinstance(operations, list):
            raise ValueError(f"{path} 必须是 YAML 列表。")
        owned = {row.get("id") for row in operations if isinstance(row, dict) and isinstance(row.get("id"), str)}
        if "llm-pi-ai" not in owned:
            operations.fa.set_block_style()
            operations.extend(row for row in defaults if row["id"] not in owned)
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=profile, delete=False) as output:
                temporary = Path(output.name)
                try:
                    yaml.dump(operations, output)
                    output.close()
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)
        marker.touch(mode=0o600, exist_ok=True)
    finally:
        lock.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="DSH 监听的具体 IP")
    parser.add_argument("--port", type=int)
    parser.add_argument("--source-dir", type=Path, help="指定其他已构建的 DSH 源码目录，默认使用当前仓库")
    parser.add_argument("--watch", action="store_true", help="监听 DSH 源码并构建 Web 前端")
    parser.add_argument("--open", action="store_true", help="在本机默认浏览器打开登录链接")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--headless", metavar="PROMPT")
    mode.add_argument("--dump-config", action="store_true")
    args = parser.parse_args()
    if args.watch and (args.headless is not None or args.dump_config):
        parser.error("--watch 只能用于 Web 对话台。")
    env = os.environ.copy()
    file_values = dotenv_values(PROJECT_ROOT / ".env")

    def setting(name, default=None):
        return env.get(name) or file_values.get(name) or default

    profile = "headless" if args.headless is not None else "web"
    key = setting("NEWAPI_API_KEY")
    if profile == "headless" and not key:
        parser.error("请在项目 .env 配置 NEWAPI_API_KEY。")

    source = args.source_dir.resolve() if args.source_dir else DSH_SOURCE_ROOT
    if not (source / "apps/cli/src/bin.ts").is_file() or not (source / "apps/web/dist/index.html").is_file():
        parser.error("请先在 DSH 仓库根目录安装依赖并运行 corepack pnpm run build:artifacts。")
    corepack = shutil.which("corepack")
    if not corepack:
        parser.error("从源码启动需要 Node.js 环境中的 Corepack。")

    data = (PROJECT_ROOT / setting("DATA_DIR", "data")).resolve()
    custom_home = setting("DSH_HOME")
    default_home = data / ("dsh-source-dev" if args.watch else "dsh")
    home = (PROJECT_ROOT / (custom_home or default_home)).resolve()
    default_workspace = home / "workspace" if args.watch else data / "agent-workspace"
    workspace = (PROJECT_ROOT / setting("DSH_PRD_WORKSPACE", str(default_workspace))).resolve()
    for directory in (home, workspace):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not custom_home:
        home.chmod(0o700)
    env.update(DSH_HOME=str(home), DSH_TELEMETRY_DISABLED="1", PYTHONUTF8="1")
    if key:
        env["NEWAPI_API_KEY"] = key
    model = setting("NEWAPI_MODEL", MODEL)
    provider = "newapi"
    selection = {"provider": provider, "model": model, "reasoningEffort": "low"}
    model_defaults = [
        {"id": "llm-pi-ai", "config": {"providers": {provider: {
            "displayName": "NewAPI", "api": "openai-completions", "apiKeyEnv": "NEWAPI_API_KEY",
            "baseURL": setting("NEWAPI_BASE_URL", BASE_URL).rstrip("/"),
            "omitDefaultMaxTokens": True,
            "retryPolicy": {"mode": "normal", "retryableCodes": [
                "EMPTY_RESPONSE", "RATE_LIMIT", "SERVER", "TIMEOUT", "TRANSPORT", "MALFORMED_RESPONSE",
            ]},
            "reasoning": "low",
            "compat": {"supportsStore": False, "supportsDeveloperRole": False,
                       "maxTokensField": "max_tokens", "thinkingFormat": "deepseek"},
            "models": [{"id": model, "contextWindow": 131_072,
                        "reasoningEfforts": {"off": None, "low": "low", "high": "high"}}],
        }}}},
        {"id": "agent-default-model", "config": selection},
    ]
    model_args = []
    if profile == "headless":
        provider_patch = write_patch(home, "newapi.agent.yml", model_defaults)
        model_args.extend(["--patch", str(provider_patch)])
    elif key:
        try:
            seed_web_model_config(home, model_defaults)
        except (OSError, ValueError) as error:
            parser.error(f"无法初始化 Web 模型配置。{error}")
    plugin_env = {
        "DATA_DIR": str(data),
        "NEWAPI_MODEL": model,
        "DSH_PRD_ATTACHMENTS": str(home / "attachments/v1/files"),
        "DSH_PRD_WORKSPACE": str(workspace),
        "DSH_PRD_REPORT_URL": setting("PRD_REPORT_URL", f"http://127.0.0.1:{REPORT_PORT}"),
        "PYTHONUTF8": "1",
    }
    feishu_secret = setting("FEISHU_APP_SECRET")
    if feishu_secret:
        env["FEISHU_APP_SECRET"] = feishu_secret
        plugin_env["FEISHU_APP_SECRET"] = "${credential:FEISHU_APP_SECRET}"
    for name in ("FEISHU_APP_ID", "FEISHU_DOMAIN"):
        if value := setting(name):
            plugin_env[name] = value
    mcp = {
        "id": "prd-review-mcp", "name": "@deepseek-ai/dsh-mcp-client",
        "config": {
            "transport": "stdio", "serverName": "prd", "command": sys.executable,
            "args": ["-m", "prd_review.agent.mcp_server"], "cwd": str(PROJECT_ROOT), "env": plugin_env,
            "toolCallTimeoutMs": 105_000, "expandTemplates": bool(feishu_secret),
        },
    }
    operations = []
    if feishu_secret:
        mcp["inject"] = ["credentials"]
        if profile == "headless":
            operations.append({"id": "credentials", "disabled": False})
    operations.append({"insert": [
        {"id": "prd-review-skills", "name": "@deepseek-ai/dsh-skill-filesystem", "config": {
            "providerName": "prd-review-skills", "includeDefaultRoots": False,
            "customSkillDirs": [str(Path(__file__).resolve().parent / "skills")],
        }},
        mcp,
    ]})
    plugin_patch = write_patch(home, "prd.agent.yml", operations)

    if args.watch:
        command = [corepack, "pnpm", "run", "dev:web", "--skip-build"]
    elif profile == "web" and not args.dump_config:
        command = [corepack, "pnpm", "run", "start:web"]
    else:
        command = [corepack, "pnpm", "dsh", profile]
    command.extend([*model_args, "--patch", str(plugin_patch)])
    if profile == "web":
        web_patch = write_patch(home, "web.agent.yml", [
            {"id": "workspace-controller", "config": {"documentsDirectory": str(workspace)}},
            {"id": "ui-chat", "config": {"transcriptView": "verbose", "linkOpening": "new-tab"}},
        ])
        command.extend(["--patch", str(web_patch)])
    if args.dump_config:
        command.append("--dump-config")
    elif args.headless is not None:
        command.extend(["--", args.headless])
    else:
        port = args.port if args.port is not None else SOURCE_PREVIEW_PORT if args.watch else AGENT_PORT
        if not 1 <= port <= 65535:
            parser.error("端口必须在 1 到 65535 之间。")
        command.extend(["--host", args.host, "--port", str(port)])
        public_url = setting("DSH_PUBLIC_URL") if not args.watch else None
        if public_url:
            command.extend(["--public-url", public_url, "--trusted-host", urlsplit(public_url).netloc])
        if not args.open:
            command.append("--no-open")
    os.chdir(source)
    os.execve(command[0], command, env)


if __name__ == "__main__":
    main()
