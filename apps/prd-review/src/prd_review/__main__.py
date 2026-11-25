"""Run the local chat and report services together."""

import argparse
from contextlib import suppress
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from dotenv import dotenv_values

from .config import AGENT_PORT, PROJECT_ROOT, REPORT_PORT


def stop_on_sigterm(signum, frame):
    raise KeyboardInterrupt


def signal_group(child, signum):
    with suppress(ProcessLookupError):
        os.killpg(child.pid, signum)


def main():
    parser = argparse.ArgumentParser(description="启动 DSH 对话与 PRD 报告工作台")
    parser.add_argument("--report-port", type=int, default=REPORT_PORT)
    parser.add_argument("--agent-port", type=int, default=AGENT_PORT)
    parser.add_argument("--source-dir", type=Path, help="使用已构建的 DSH 源码运行聊天服务")
    parser.add_argument("--open", action="store_true", help="在默认浏览器打开 DSH 登录链接")
    args = parser.parse_args()
    if not (1 <= args.report_port <= 65535 and 1 <= args.agent_port <= 65535):
        parser.error("端口必须在 1 到 65535 之间。")
    if args.report_port == args.agent_port:
        parser.error("聊天与报告工作台需要使用不同端口。")

    env = os.environ.copy()
    settings = dotenv_values(PROJECT_ROOT / ".env")
    if not env.get("PRD_REPORT_URL") and not settings.get("PRD_REPORT_URL"):
        env["PRD_REPORT_URL"] = f"http://127.0.0.1:{args.report_port}"
    report = [sys.executable, "-m", "gunicorn", "--no-control-socket",
              "--bind", f"127.0.0.1:{args.report_port}", "--threads", "4", "--timeout", "300",
              "prd_review.web.app:create_app()"]
    agent = [sys.executable, "-m", "prd_review.agent.launcher", "--port", str(args.agent_port)]
    if args.source_dir:
        agent.extend(["--source-dir", str(args.source_dir.resolve())])
    if args.open:
        agent.append("--open")

    signal.signal(signal.SIGTERM, stop_on_sigterm)
    children = []
    try:
        for command in (report, agent):
            children.append(subprocess.Popen(command, cwd=PROJECT_ROOT, env=env, start_new_session=True))
        while all(child.poll() is None for child in children):
            time.sleep(0.2)
        return next((abs(child.returncode) for child in children if child.returncode), 0)
    except KeyboardInterrupt:
        return 0
    finally:
        for child in children:
            signal_group(child, signal.SIGTERM)
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                signal_group(child, signal.SIGKILL)
                child.wait()


if __name__ == "__main__":
    sys.exit(main())
