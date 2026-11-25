"""Read Feishu documents with the local Lark CLI."""

import json
import os
import re
import subprocess
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from ..config import NODE_MODULES


class FeishuScopeMissing(ValueError):
    pass


def fetch_doc(link, identity="user", env=None):
    result = subprocess.run(
        ["lark-cli", "docs", "+fetch", "--doc", link, "--doc-format", "markdown", "--as", identity],
        capture_output=True, text=True, encoding="utf-8",
        timeout=90, check=False, env=env,
    )
    if result.returncode:
        try:
            error = json.loads(result.stderr).get("error")
        except (ValueError, AttributeError):
            error = {}
        if identity == "bot" and isinstance(error, dict) and error.get("code") == 99991672:
            if urlsplit(link).path.startswith("/wiki/"):
                detail = "请确认应用的云文档只读权限（docx:document:readonly）和知识库节点读取权限（wiki:node:read）已生效。"
            else:
                detail = "请确认应用的云文档只读权限（docx:document:readonly）已生效。"
            raise FeishuScopeMissing(f"飞书应用缺少接口权限（99991672）。{detail}")
        raise ValueError(f"飞书 CLI 失败。{result.stderr.strip() or result.returncode}")
    response = json.loads(result.stdout)
    if response.get("ok") is not True:
        raise ValueError(f"飞书 CLI 未返回成功。{response.get('error', '')}")
    doc = response["data"]["document"]
    if not doc.get("document_id") or not doc.get("revision_id"):
        raise ValueError("飞书响应缺少文档 ID 或版本号")
    return doc["document_id"], str(doc["revision_id"]), link, doc["content"]


def feishu_link(value, allowed_domain=None):
    url = urlsplit(value.strip())
    valid_host = url.hostname and url.hostname.endswith((".feishu.cn", ".larksuite.com"))
    if (url.scheme != "https" or not valid_host or url.username or url.password or url.port
            or not re.fullmatch(r"/(?:wiki|docx)/[a-zA-Z0-9]+/?", url.path)):
        raise ValueError("请输入飞书 /docx/ 或 /wiki/ 文档链接")
    if allowed_domain and url.hostname != allowed_domain:
        raise ValueError("此链接不属于已配置的飞书企业域名")
    return urlunsplit((url.scheme, url.hostname, url.path.rstrip("/"), "", ""))


def tenant_access_token(app_id, app_secret):
    body = urlencode({"grant_type": "client_credentials", "client_id": app_id,
                      "client_secret": app_secret}).encode()
    token_request = Request("https://accounts.feishu.cn/oauth/v3/token", data=body,
                            headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urlopen(token_request, timeout=10) as response:
            result = json.load(response)
    except HTTPError as error:
        with error:
            try:
                result = json.load(error)
            except ValueError:
                result = {}
    except (URLError, TimeoutError, ValueError) as error:
        raise ValueError("无法连接飞书认证服务，请检查服务端网络后重试。") from error
    if not isinstance(result, dict):
        raise ValueError("飞书认证服务返回了无效结果。")
    token = result.get("access_token")
    if result.get("code") == 0 and isinstance(token, str) and token:
        return token
    if result.get("code") in (20009, 20069):
        raise ValueError("飞书应用尚未在企业内安装或启用，请检查应用发布状态。")
    raise ValueError("无法获取飞书应用令牌，请检查 App ID、App Secret 和应用状态。")


def lark_options():
    app_id = os.environ.get("FEISHU_APP_ID", "")
    app_secret = os.environ.get("FEISHU_APP_SECRET", "")
    if not app_id or not app_secret:
        raise ValueError("飞书只读连接尚未配置。你可以先用「粘贴正文」评审。")
    token = tenant_access_token(app_id, app_secret)
    inherited = ("PATH", "HOME", "TMPDIR", "SSL_CERT_FILE", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY")
    env = {key: os.environ[key] for key in inherited if key in os.environ}
    env["PATH"] = os.pathsep.join((str(NODE_MODULES / ".bin"), env.get("PATH", os.defpath)))
    env.update(
        LARKSUITE_CLI_APP_ID=app_id, LARKSUITE_CLI_TENANT_ACCESS_TOKEN=token,
        LARKSUITE_CLI_BRAND="feishu", LARKSUITE_CLI_STRICT_MODE="bot",
        LARKSUITE_CLI_NO_UPDATE_NOTIFIER="1", LARKSUITE_CLI_NO_SKILLS_NOTIFIER="1",
    )
    return {"identity": "bot", "env": env}


def read_feishu_document(value, allowed_domain=None):
    link = feishu_link(value, allowed_domain)
    options = lark_options()
    try:
        doc_id, revision, source_url, source = fetch_doc(link, **options)
        return doc_id, int(revision), source_url, source
    except FeishuScopeMissing:
        raise
    except (ValueError, OSError, KeyError, TypeError, subprocess.TimeoutExpired):
        raise ValueError("飞书读取失败。请检查只读权限和文档授权，或先粘贴正文。") from None
