"""Read-only source tools and local report persistence for the DSH PRD agent."""

import asyncio
import json
import logging
import os
from pathlib import Path
from uuid import uuid4

from anyio import CapacityLimiter, to_thread
from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import MCPError

from .. import review as review_rules
from ..config import MODEL, REPORT_PORT, data_directory
from ..integrations.feishu import read_feishu_document
from ..store import RevisionChanged, Store


STORE = Store(data_directory() / "reviews.sqlite3")
REPORT_BASE = os.getenv("DSH_PRD_REPORT_URL", f"http://127.0.0.1:{REPORT_PORT}").rstrip("/")
ATTACHMENTS = os.getenv("DSH_PRD_ATTACHMENTS")
WORKSPACE = os.getenv("DSH_PRD_WORKSPACE")
MODEL_ID = os.getenv("NEWAPI_MODEL", MODEL)
DRAFTS = {}
MAX_SOURCE = 180_000
READ_BUDGET = 16_000
logger = logging.getLogger(__name__)

TEXT = {"type": "string", "minLength": 1}
POSITIVE = {"type": "integer", "minimum": 1}
MODULE = {"type": ["string", "null"], "pattern": "^M[0-9]{2,4}$"}
ANCHOR = {"type": ["integer", "null"], "minimum": 1}


def obj(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required),
            "additionalProperties": False}


def array(items, minimum=0):
    return {"type": "array", "items": items, "minItems": minimum}


def record(**properties):
    return obj(properties, properties)


NODE_ID = {"type": "string", "pattern": f"^{review_rules.NODE_ID.pattern}$", "description": "大写 N 加正整数，如 N1、N2"}
NODE = record(id=NODE_ID, text=TEXT, decision={"type": "boolean"})
EDGE = record(**{"from": NODE_ID, "to": NODE_ID, "label": {"type": "string"}})
FLOW = {"anyOf": [record(nodes=array(NODE, 2), edges=array(EDGE, 1)), {"type": "null"}]}
EVIDENCE = obj({"line": POSITIVE, "end_line": POSITIVE, "quote": TEXT}, ["line", "quote"])
SECTION = record(title=TEXT, anchor=ANCHOR, intro=TEXT, steps=array(TEXT), flow=FLOW)
ISSUE = record(
    id=TEXT, module=MODULE, category={"enum": list(review_rules.ISSUE_TYPES)},
    status={"enum": list(review_rules.STATUSES)}, question=TEXT, impact=TEXT, source=TEXT,
    evidence=array(EVIDENCE),
)
CASE = record(
    module=MODULE, title=TEXT, priority={"enum": ["P0", "P1", "P2"]},
    preconditions=TEXT, steps=array(TEXT, 1), expected={"anyOf": [TEXT, array(TEXT, 1)]}, evidence=array(EVIDENCE, 1),
)


def get_version(version_id, connection=None):
    row = STORE.get(version_id, connection)
    if not row:
        raise ValueError("找不到该评审版本，可先查本地文档列表。")
    return row


def links(version_id, line=None, left=None):
    report = f"{REPORT_BASE}/versions/{version_id}"
    original = f"{report}/compare?mode=source&view=original"
    output = {"report_url": report, "source_compare_url": f"{original}#line-left-{line}" if line else original,
              "markdown_url": f"{report}/markdown"}
    if left:
        output["history_compare_url"] = f"{report}/compare?mode=history&left={left}&right={version_id}&view=original"
    return output


def with_case_export(output):
    if output["test_cases"]:
        return output | {"xmind_url": f"{output['report_url']}/xmind"}
    return output


def list_reviews():
    return {"documents": [{"version_id": row["id"], "title": row["title"], "revision": row["revision"],
                           "report_url": links(row["id"])["report_url"]} for row in STORE.all_documents()[:40]]}


def read_document_file(file_path, allowed_roots):
    path = Path(file_path)
    extensions = {".md", ".markdown", ".txt"}
    if not path.is_absolute():
        raise ValueError("请提供 DSH 附件或工作区文件的绝对路径，也可以直接粘贴正文。")
    if path.suffix.lower() not in extensions:
        raise ValueError("请提供 UTF-8 的 Markdown 或文本文件。")
    try:
        path = path.resolve()
        if not any(path.is_relative_to(Path(root).resolve()) for root in allowed_roots if root):
            raise ValueError("正文文件须位于指定的 DSH 工作区或对话附件目录内。")
        if path.suffix.lower() not in extensions or not path.is_file():
            raise ValueError("请提供可以读取的 Markdown 或文本文件。")
        with path.open("rb") as file:
            content = file.read(MAX_SOURCE * 4 + 4)
    except (OSError, RuntimeError):
        raise ValueError("无法读取该正文文件，请检查文件路径和权限。") from None
    if len(content) > MAX_SOURCE * 4 + 3:
        raise ValueError(f"正文最多支持 {MAX_SOURCE:,} 个字符。")
    try:
        return content.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError:
        raise ValueError("该文件无法按 UTF-8 读取，请转换编码或粘贴正文。") from None


def open_document(version_id=None, feishu_url=None, text=None, previous_version_id=None, review_guidance=None,
                  attachment_path=None, workspace_path=None):
    file_path = workspace_path if workspace_path is not None else attachment_path
    if sum(value is not None for value in (version_id, feishu_url, text, attachment_path, workspace_path)) != 1:
        raise ValueError("请从本地版本、飞书链接、粘贴正文、对话附件和工作区文件中选择一个来源。")
    if previous_version_id is not None and text is None and file_path is None:
        raise ValueError("previous_version_id 用于将粘贴或文件导入的新正文追加到原有文档。")
    selected = previous = None
    if version_id is not None:
        selected = get_version(version_id)
        snapshot = selected["snapshot"]
        doc_id, revision, origin = selected["document_id"], selected["revision"], snapshot["origin"]
        source_url, source = snapshot["source_url"], snapshot["source"]
    elif feishu_url is not None:
        doc_id, revision, source_url, source = read_feishu_document(feishu_url, os.getenv("FEISHU_DOMAIN") or None)
        origin = "feishu"
        current = STORE.current(doc_id, revision)
        selected = get_version(current) if current else None
    else:
        # Existing chats may have put a workspace file in attachment_path.
        roots = (WORKSPACE,) if workspace_path is not None else (ATTACHMENTS, WORKSPACE)
        source = read_document_file(file_path, roots) if file_path is not None else text.strip()
        origin, source_url = "paste", ""
        if previous_version_id is not None:
            previous = get_version(previous_version_id)
            if previous["snapshot"]["origin"] != "paste":
                raise ValueError("请用原飞书链接读取飞书文档的新版本。")
            doc_id = previous["document_id"]
            if STORE.history(doc_id)[0]["id"] != previous_version_id:
                raise ValueError("请选择该文档最新的本地版本作为基准。")
            revision, source_url = previous["revision"] + 1, previous["snapshot"]["source_url"]
            if source == previous["snapshot"]["source"]:
                selected, previous, revision = previous, None, previous["revision"]
        else:
            doc_id, revision = f"paste:{uuid4().hex}", 1

    if not source.strip() or len(source) > MAX_SOURCE:
        raise ValueError(f"正文不能为空，且最多支持 {MAX_SOURCE:,} 个字符。")
    if review_guidance is not None and len(review_guidance) > 12_000:
        raise ValueError("附加评审规则最多支持 12,000 个字符。")
    if selected and selected["snapshot"]["source"] != source:
        raise ValueError("飞书版本号相同但原文不同，无法覆盖已归档的原文。")
    history = STORE.history(doc_id)
    older = next((row for row in history if row["revision"] < revision), None)
    if older and previous is None:
        previous = get_version(older["id"])
    reference = selected or previous
    saved_report = reference["snapshot"]["review"] if reference else {}
    same_source = bool(selected)
    if selected and saved_report.get("modules") and not saved_report.get("audit_version"):
        saved_report = previous["snapshot"]["review"] if previous else {}
        same_source = False
    guidance = reference["snapshot"].get("skill") if reference else None
    if review_guidance is not None:
        guidance = {"name": "对话附加规则", "instructions": review_guidance.strip()}
    payload = review_rules.review_payload(source, saved_report.get("issues", []), same_source, guidance)
    token = uuid4().hex
    DRAFTS[token] = {
        "payload": payload, "doc_id": doc_id, "revision": revision,
        "source_url": source_url, "origin": origin, "selected": selected, "previous": previous, "batches": {},
    }
    if len(DRAFTS) > 32:
        DRAFTS.pop(next(iter(DRAFTS)))
    output = {
        "document": token, "line_count": len(source.splitlines()), "revision": revision,
        "module_outline": [{"id": f"M{i:02d}", **entry} for i, entry in enumerate(payload["module_outline"], 1)],
        "same_source": same_source,
        "prior_issues": [{key: issue[key] for key in ("id", "question", "status")}
                         for issue in payload["previous_issues"]],
        "review_guidance": guidance,
    }
    if selected:
        output.update({"saved_version_id": selected["id"], **links(selected["id"])})
    return output


def get_draft(document):
    draft = STORE.get_draft(document) or DRAFTS.get(document)
    if draft is None:
        raise ValueError("文档会话已经过期，请在对话中重新打开同一份文档。")
    return draft


def read_lines(document, start_line=1, limit=100):
    draft = get_draft(document)
    lines = draft["payload"]["source"].splitlines()
    if type(start_line) is not int or type(limit) is not int or not 1 <= start_line <= len(lines) or limit < 1:
        raise ValueError("请输入正文范围内的开始行与正数行数。")
    result, size = [], 0
    for index in range(start_line - 1, min(len(lines), start_line - 1 + min(limit, 180))):
        line = lines[index]
        if result and size + len(line) > READ_BUDGET:
            break
        if len(line) > READ_BUDGET:
            raise ValueError(f"第 {index + 1} 行超过 {READ_BUDGET:,} 字符，请把这一行分开后重新导入。")
        result.append(f"[L{index + 1}] {line}")
        size += len(line) + 10
    end = start_line + len(result) - 1
    return {"numbered_source": "\n".join(result), "end_line": end,
            "next_start_line": end + 1 if end < len(lines) else None,
            "staged_batches": sorted(map(int, draft["batches"]))}


def add_review_batch(document, batch, issues, test_cases, modules=None):
    if modules is None:
        modules = []
    draft = get_draft(document)
    if draft.get("saved"):
        raise ValueError("本次评审已保存，如要重新评审，请重新打开文档。")
    if type(batch) is not int or not 1 <= batch <= 100:
        raise ValueError("批次编号必须在 1 到 100 之间。")
    outline = draft["payload"]["module_outline"]
    valid_anchors = {entry["anchor"] for entry in outline}
    for section in modules:
        if not outline:
            section["anchor"] = None
        elif section["anchor"] is not None and section["anchor"] not in valid_anchors:
            raise ValueError(f"模块「{section['title']}」的 anchor 不在模块目录内。")
        if section.get("flow"):
            for node in section["flow"]["nodes"]:
                if not review_rules.NODE_ID.fullmatch(node["id"]):
                    raise ValueError(f"模块「{section['title']}」的流程节点 {node['id']} 格式无效，请使用大写 N1、N2。")
        review_rules.validate_section(section)
    for index, case in enumerate(test_cases, 1):
        try:
            review_rules.case_details(case)
        except ValueError as error:
            raise ValueError(f"第 {batch} 批第 {index} 条测试用例：{error}") from error
    resolve = review_rules.evidence_resolver(draft["payload"]["source"])
    previous_ids = {item["id"] for item in draft["payload"]["previous_issues"]}
    for entry in [*issues, *test_cases]:
        citations = entry["evidence"]
        required = ("id" not in entry or entry["status"] == "resolved" or entry["id"] not in previous_ids
                    or not draft["payload"]["same_source"] and entry["status"] == "open")
        for index, citation in enumerate(citations):
            verified = resolve([citation])
            if not verified:
                raise ValueError(f"证据第 {citation['line']} 行与所引原文不匹配，请重读原文并修正。")
            citations[index] = verified[0]
        if required and not citations:
            raise ValueError(f"请为「{entry.get('id') or entry['title']}」提供可核对的原文证据。")
    batches = draft["batches"] | {str(batch): {"modules": modules, "issues": issues, "test_cases": test_cases}}
    STORE.save_draft(document, draft | {"batches": batches}, expected=draft if draft["batches"] else None)
    DRAFTS.pop(document, None)
    return {"batch": batch, "staged_batches": sorted(map(int, batches)),
            "modules": len(modules), "issues": len(issues), "test_cases": len(test_cases)}


def save_review(document, title, intro):
    draft = get_draft(document)
    if draft.get("saved"):
        return with_case_export(draft["saved"])
    result = {"title": review_rules.clean_text(title), "intro": review_rules.clean_text(intro), "modules": []}
    audit = {"issues": [], "test_cases": []}
    indices = sorted(map(int, draft["batches"]))
    if not indices or indices != list(range(1, indices[-1] + 1)):
        raise ValueError("请补齐从 1 开始的评审批次，再保存。")
    for key in ("modules", "issues", "test_cases"):
        entries = [entry for index in indices for entry in draft["batches"][str(index)][key]]
        target = result if key == "modules" else audit
        # The report validator normalizes these records in place. Keep the batches retryable.
        target[key] = json.loads(json.dumps(entries, ensure_ascii=False))

    payload = draft["payload"]
    report = review_rules.audit_report(result, audit, payload)
    selected, previous = draft["selected"], draft["previous"]
    revision = draft["revision"]
    marker = next((item["evidence"][0]["line"] for item in report["issues"] if item["evidence"]), None)
    with STORE.transaction() as db:
        if selected:
            version_id = selected["id"]
            STORE.reformat(version_id, report, f"DSH · {MODEL_ID}", payload["review_skill"],
                           selected["snapshot"]["review"], connection=db)
        else:
            previous_revision = previous["revision"] if previous else None
            version_id = STORE.save({
                "doc_id": draft["doc_id"], "revision": str(revision),
                "source_url": draft["source_url"], "source": payload["source"], "origin": draft["origin"],
                "previous_revision": previous_revision, "engine": f"DSH · {MODEL_ID}",
                "review": report, "skill": payload["review_skill"],
            }, previous_revision, connection=db)
            # Another process can save the same source revision while this review is in progress.
            if get_version(version_id, db)["snapshot"]["review"] != report:
                raise RevisionChanged
        output = {"version_id": version_id, "revision": revision, "modules": len(report["modules"]),
                  "issues": len(report["issues"]), "test_cases": len(report["test_cases"]),
                  **links(version_id, line=marker, left=previous["id"] if previous else None)}
        STORE.save_draft(document, draft | {"saved": output}, expected=draft, connection=db)
    return with_case_export(output)


def get_review(version_id):
    row = get_version(version_id)
    review = row["snapshot"]["review"]
    history = STORE.history(row["document_id"])
    issues = []
    for issue in review["issues"]:
        evidence = issue.get("evidence", [])
        permalink = links(version_id, line=evidence[0]["line"])["source_compare_url"] if evidence else None
        issues.append({**issue, "original_url": permalink})
    return with_case_export({"version_id": version_id, "revision": row["revision"], "title": row["title"],
                             "modules": [{"title": item["title"], "anchor": item.get("anchor")}
                                         for item in review.get("modules", [])],
                             "issues": issues, "test_cases": len(review.get("test_cases", [])),
                             "history": [dict(entry) for entry in history], **links(version_id)})


def compare_versions(left_version_id, right_version_id):
    left, right = get_version(left_version_id), get_version(right_version_id)
    if left["document_id"] != right["document_id"] or left["id"] == right["id"]:
        raise ValueError("请选择同一份文档的两个不同版本。")
    if left["revision"] > right["revision"]:
        left, right = right, left
    return {"left_version_id": left["id"], "right_version_id": right["id"],
            **links(right["id"], left=left["id"])}


TOOLS = {
    "list_reviews": (list_reviews, "列出 PRD 工作台当前已归档的文档及各自最新版本。", obj({})),
    "open_document": (
        open_document, "打开本地评审快照、只读飞书 PRD、粘贴正文、聊天附件或工作区文件，建立可读取真实行号的评审会话。"
        "MD/TXT 聊天附件的绝对路径传给 attachment_path，DSH 工作区内文件的绝对路径传给 workspace_path。"
        "用 review_guidance 传入附加评审规则。",
        obj({"version_id": POSITIVE, "feishu_url": TEXT, "text": TEXT,
             "attachment_path": TEXT, "workspace_path": TEXT, "previous_version_id": POSITIVE, "review_guidance": TEXT}),
    ),
    "read_lines": (read_lines, "分段读 PRD 原文并保留真实行号，同时返回已通过的评审批次。继续读 next_start_line 直到为空。",
                   obj({"document": TEXT, "start_line": POSITIVE, "limit": POSITIVE}, ["document"])),
    "get_review": (get_review, "查看本地已存评审、问题原文链接、测试用例总数、XMind 下载链接和版本历史。",
                   record(version_id=POSITIVE)),
    "add_review_batch": (
        add_review_batch, "仅评审任务使用。暂存一批连续模块及其问题和测试用例。"
        "批次从 1 开始；同编号可覆盖重试。流程节点用大写 N1、N2。"
        "问题使用跨批次连续 Q-01 编号；每条证据引用真实行号和原文片段。",
        obj({"document": TEXT, "batch": POSITIVE,
             "modules": array(SECTION) | {"description": "只补充问题或用例时可省略"},
             "issues": array(ISSUE), "test_cases": array(CASE)}, ["document", "batch", "issues", "test_cases"]),
    ),
    "save_review": (save_review, "验证并保存评审，返回报告及原文定位链接。有用例时提供 XMind 下载链接。",
                    record(document=TEXT, title=TEXT, intro=TEXT)),
    "compare_versions": (compare_versions, "返回同一份文档两个历史版本的左右对比链接。",
                         record(left_version_id=POSITIVE, right_version_id=POSITIVE)),
}


INPUT_VALIDATORS = {name: Draft202012Validator(tool[2]) for name, tool in TOOLS.items()}


def tool_error(code, message):
    return types.CallToolResult(is_error=True, content=[types.TextContent(type="text", text=message)],
                                meta={"prdError": {"code": code}})


def call_tool(name, arguments):
    tool = TOOLS.get(name)
    if tool is None:
        raise MCPError(types.INVALID_PARAMS, "未知 PRD 工具。请重新获取工具列表。")
    error = best_match(INPUT_VALIDATORS[name].iter_errors(arguments))
    if error is not None:
        fields = "参数" if not error.absolute_path else error.json_path
        if error.validator == "required":
            missing = ", ".join(key for key in error.validator_value if key not in error.instance)
            message = f"{fields} 缺少必填字段 {missing}。"
        else:
            message = f"{fields} 不符合工具的 {error.validator} 规则，请按工具定义修正。"
        return tool_error("INVALID_ARGS", message)
    try:
        data = tool[0](**arguments)
    except RevisionChanged:
        return tool_error("PRD_REVISION_CHANGED", "评审期间该文档已更新，请重新打开最新版本后提交。")
    except ValueError as error:
        return tool_error("PRD_REJECTED", str(error))
    except Exception as error:
        logger.error("PRD tool %s failed (%s)", name, type(error).__name__)
        raise MCPError(types.INTERNAL_ERROR, "PRD 工具发生内部错误，请查看服务端日志。") from error
    return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))])


async def list_tools(_context, _params):
    return types.ListToolsResult(tools=[types.Tool(name=name, description=description, input_schema=schema)
                                        for name, (_, description, schema) in TOOLS.items()])


async def serve():
    # The process also owns unsaved drafts. Keep synchronous calls serial through cancellation.
    limiter = CapacityLimiter(1)

    async def invoke_tool(_context, params):
        return await to_thread.run_sync(call_tool, params.name, params.arguments or {}, limiter=limiter)

    server = Server("prd-review", version="1.0.0", instructions="产品需求评审流程见 prd-review Skill。",
                    on_list_tools=list_tools, on_call_tool=invoke_tool)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(serve())
