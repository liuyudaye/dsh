from collections import defaultdict
from io import BytesIO
import json
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile


def topic(title, children=(), **properties):
    node = {"id": uuid4().hex, "class": "topic", "title": title, **properties}
    if children:
        node["children"] = {"attached": children}
    return node


def case_topic(case, source_url):
    sections = [
        topic("前置条件", [topic(case["preconditions"])]),
        topic("执行步骤", [topic(f"{index}. {step}") for index, step in enumerate(case["steps"], 1)]),
        topic("预期结果", [topic(case["expected"])]),
    ]
    citations = []
    for cite in case.get("evidence", []):
        line, end_line = cite["line"], cite["end_line"]
        span = str(line) if line == end_line else f"{line}–{end_line}"
        citations.append(topic(f"第 {span} 行 · {cite['quote']}", href=source_url(line)))
    if citations:
        sections.append(topic("原文出处", citations))
    return topic(f"{case['id']} · {case['priority']} · {case['title']}", sections)


def export_cases(review, source_url):
    modules = [module["title"] for module in review.get("modules", [])]
    module_numbers = range(1, len(modules) + 1)
    groups, shared = defaultdict(list), []
    for case in review["test_cases"]:
        number = case.get("module")
        cases = groups[number] if number in module_numbers else shared
        cases.append(case_topic(case, source_url))
    branches = [topic(title, groups[number]) for number, title in enumerate(modules, 1) if number in groups]
    if shared:
        branches.append(topic("跨模块用例", shared))

    root = topic(f"{review['title']} · 测试用例", branches, structureClass="org.xmind.ui.logic.right")
    sheet_id = uuid4().hex
    entries = {
        "content.json": [{"id": sheet_id, "class": "sheet", "title": "测试用例", "rootTopic": root}],
        "metadata.json": {"creator": {"name": "PRD Review"}, "dataStructureVersion": "2"},
    }
    entries["manifest.json"] = {"file-entries": dict.fromkeys(entries, {})}
    file = BytesIO()
    with ZipFile(file, "w", ZIP_DEFLATED) as archive:
        for name, contents in entries.items():
            archive.writestr(name, json.dumps(contents, ensure_ascii=False).encode("utf-8"))
    file.seek(0)
    return file
