"""Project paths and defaults shared by the two services."""

import os
from pathlib import Path


def project_root():
    configured = os.getenv("PRD_PROJECT_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()

    package_parent = Path(__file__).resolve().parents[1]
    if package_parent.name == "src" and (package_parent.parent / "pyproject.toml").is_file():
        return package_parent.parent
    return Path.cwd()


PROJECT_ROOT = project_root()
DSH_SOURCE_ROOT = PROJECT_ROOT.parents[1]
NODE_MODULES = PROJECT_ROOT / "assets/node_modules"
MODEL = "deepseek-v4.1-flash-1017-v147"
BASE_URL = "https://apihub.bookln.cn/v1"
REPORT_PORT = 8000
AGENT_PORT = 3080


def data_directory():
    return (PROJECT_ROOT / (os.getenv("DATA_DIR") or "data")).resolve()
