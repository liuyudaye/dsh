"""Shared requirements, evidence validation, and report formatting."""

import re


ISSUE_ID = re.compile(r"Q-([0-9]{2,})")
NODE_ID = re.compile(r"N[1-9][0-9]*")
STATUSES = {"open": "待澄清", "resolved": "已解决", "unverified": "未核验"}
ISSUE_TYPES = {"ambiguity": "需求歧义", "conflict": "规则冲突", "risk": "设计风险", "pending": "原文明示待确认"}


PARENT_HEADING = re.compile(r"([1-9]\d*)[.．、][ \t]+([^\d\s].{1,85})")
CHILD_HEADING = re.compile(r"([1-9]\d*)\.[1-9]\d*[.．、]?[ \t]+[^\d\s].{1,55}")
MARKDOWN_HEADING = re.compile(r"(#{1,6})\s+(.+?)\s*#*")


def module_outline(source):
    parents = {}
    detected = {}
    markdown = []
    fence = None
    for line_number, line in enumerate(source.splitlines(), 1):
        line = line.strip()
        marker = re.match(r"(```+|~~~+)", line)
        if marker:
            if fence is None:
                fence = marker[0]
            elif line.startswith(fence):
                fence = None
            continue
        if fence:
            continue
        headline = MARKDOWN_HEADING.fullmatch(line)
        if headline:
            line = headline[2]
            markdown.append((len(headline[1]), line_number, line))
        parent = PARENT_HEADING.fullmatch(line)
        if parent:
            parents[parent[1]] = (line_number, line)
        child = CHILD_HEADING.fullmatch(line)
        if child and child[1] in parents:
            detected.setdefault(child[1], parents[child[1]])
    numbered = sorted(detected.values())
    if len(numbered) >= 2:
        return [{"anchor": line, "title": title} for line, title in numbered]
    if markdown:
        levels = sorted({level for level, _, _ in markdown})
        for level in levels:
            similar = [(line, title) for depth, line, title in markdown if depth == level]
            if len(similar) >= 2:
                return [{"anchor": line, "title": title} for line, title in similar]
    return []


def review_payload(source, previous, same_source=False, skill=None):
    return {"module_outline": module_outline(source), "source": source, "previous_issues": previous,
            "same_source": same_source, "review_skill": skill}


def clean_text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("模型返回了空文本或非文本字段")
    return " ".join(value.split())


def validate_review(review, previous, outline=(), same_source=False, require_modules=False):
    if not isinstance(review, dict):
        raise ValueError("模型必须返回 JSON 对象")
    clean_text(review["title"])
    clean_text(review["intro"])
    modules = review.get("modules")
    if modules is None and require_modules:
        raise ValueError("评审缺少模块")
    if modules is not None:
        if not isinstance(modules, list) or not modules:
            raise ValueError("模块必须是非空数组")
        expected = {entry["anchor"]: entry["title"] for entry in outline}
        anchors = []
        for module in modules:
            anchor = module.get("anchor")
            if anchor is not None:
                if type(anchor) is not int or anchor not in expected:
                    raise ValueError("模块原文位置无效")
                anchors.append(anchor)
                module["title"] = expected[anchor]
            clean_text(module["title"])
            validate_section(module)
        if anchors != list(expected):
            raise ValueError("评审模块有遗漏、重复或顺序错乱")
    else:
        validate_section(review)
    if not isinstance(review["issues"], list):
        raise ValueError("issues 必须是数组")
    old_issues = {item["id"]: item for item in previous}
    old_ids = old_issues.keys()
    highest = max((int(ISSUE_ID.fullmatch(i)[1]) for i in old_ids), default=0)
    seen = set()
    new_numbers = set()
    for issue in review["issues"]:
        module_number = issue.get("module")
        if module_number is not None and (
            type(module_number) is not int or not modules or not 1 <= module_number <= len(modules)
        ):
            raise ValueError("问题指向了不存在的模块")
        number = ISSUE_ID.fullmatch(issue["id"])
        if not number or issue["id"] in seen or issue["status"] not in STATUSES:
            raise ValueError("问题编号重复或问题状态无效")
        if issue["id"] not in old_ids and (
            int(number[1]) <= highest or issue["id"] != f"Q-{int(number[1]):02d}"
            or issue["status"] == "resolved"
        ):
            raise ValueError("新增问题需要按顺序取号，并提供待澄清或未核验状态")
        if issue["id"] not in old_ids:
            new_numbers.add(int(number[1]))
        seen.add(issue["id"])
        clean_text(issue["question"])
        clean_text(issue["source"])
        if issue["id"] in old_issues:
            issue["question"] = old_issues[issue["id"]]["question"]
            if same_source:
                issue["status"] = old_issues[issue["id"]]["status"]
    if not old_ids <= seen:
        raise ValueError("复审缺少旧问题编号")
    if new_numbers != set(range(highest + 1, highest + len(new_numbers) + 1)):
        raise ValueError("新增问题编号必须连续")
    return review


def validate_section(section):
    clean_text(section["intro"])
    if not isinstance(section["steps"], list):
        raise ValueError("steps 必须是数组")
    for step in section["steps"]:
        clean_text(step)
    if section.get("flow") is not None:
        mermaid(section["flow"])


def mermaid(flow):
    ids = set()
    lines = ["flowchart TD"]
    for node in flow["nodes"]:
        key = node["id"]
        if not NODE_ID.fullmatch(key) or key in ids or not isinstance(node["decision"], bool):
            raise ValueError("流程节点编号重复或格式错误")
        ids.add(key)
        text = diagram_text(node["text"])
        begin, end = ("{", "}") if node["decision"] else ("[", "]")
        lines.append(f'    {key}{begin}"{text}"{end}')
    if not flow["edges"]:
        raise ValueError("有流程图时至少需要一条连线")
    for edge in flow["edges"]:
        if edge["from"] not in ids or edge["to"] not in ids:
            raise ValueError("流程图中的连线引用了不存在的节点")
        label = diagram_text(edge["label"]) if edge["label"] else ""
        branch = f'|"{label}"|' if label else ""
        lines.append(f'    {edge["from"]} -->{branch} {edge["to"]}')
    return "\n".join(lines)


def diagram_text(value):
    # Mermaid entity syntax keeps generated labels out of the diagram grammar.
    sensitive = set('\"\\|<>[]{}#&`')
    return "".join(f"#{ord(c)};" if c in sensitive else c for c in clean_text(value))


def evidence_resolver(source):
    lines = source.splitlines()
    normalized = [normalize_quote(line) for line in lines]

    def resolve(citations):
        verified = []
        if not isinstance(citations, list):
            return verified
        for entry in citations:
            if not isinstance(entry, dict) or not isinstance(entry.get("quote"), str):
                continue
            quote = entry["quote"].strip()
            needle = normalize_quote(quote)
            if len(needle) < 4 or len(needle) > 260:
                continue
            start = entry.get("line")
            end = entry.get("end_line", start)
            if not (type(start) is int and type(end) is int and 1 <= start <= end <= len(lines)
                    and end - start <= 7 and needle in "".join(normalized[start - 1:end])):
                matches = [i for i, line in enumerate(normalized, 1) if needle in line]
                if len(matches) != 1:
                    continue
                start = end = matches[0]
            citation = {"line": start, "end_line": end, "quote": quote}
            if citation not in verified:
                verified.append(citation)
        return verified

    return resolve


def normalize_quote(text):
    return re.sub(r"\s+", "", text)


def resolve_module(value, evidence, modules):
    boundary = [(module["anchor"], index) for index, module in enumerate(modules, 1)
                if module.get("anchor") is not None]
    locations = {
        index for cite in evidence for line in (cite["line"], cite["end_line"])
        if (index := next((index for start, index in reversed(boundary) if start <= line), None)) is not None
    }
    if locations:
        return next(iter(locations)) if len(locations) == 1 else None
    if isinstance(value, str):
        match = re.fullmatch(r"M([0-9]{1,4})", value)
        value = int(match[1]) if match else None
    return value if type(value) is int and 1 <= value <= len(modules) else None


def case_details(case):
    steps = case.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("测试用例缺少执行步骤（steps）")
    expected = case.get("expected")
    try:
        if isinstance(expected, list) and expected:
            expected = "；".join(clean_text(item) for item in expected)
        else:
            expected = clean_text(expected)
    except ValueError as error:
        raise ValueError("预期结果（expected）须为非空文本或非空文本列表") from error
    return {"title": clean_text(case.get("title")), "preconditions": clean_text(case.get("preconditions")),
            "steps": [clean_text(step) for step in steps], "expected": expected}


def audit_report(draft, audit, payload):
    if not isinstance(audit, dict) or not all(isinstance(audit.get(key), list) for key in ("issues", "test_cases")):
        raise ValueError("审阅结果缺少问题或用例列表")
    report = draft | {"issues": audit["issues"], "test_cases": [], "audit_version": 1}
    resolve = evidence_resolver(payload["source"])
    for issue in report["issues"]:
        issue["category"] = issue.get("category") if issue.get("category") in ISSUE_TYPES else "ambiguity"
        issue["impact"] = str(issue.get("impact") or "").strip()
        issue["evidence"] = resolve(issue.get("evidence"))
        issue["module"] = resolve_module(issue.get("module"), issue["evidence"], report["modules"])
    validate_review(report, payload["previous_issues"], payload["module_outline"],
                    same_source=payload["same_source"], require_modules=True)
    for entry in audit["test_cases"]:
        evidence = resolve(entry.get("evidence"))
        module = resolve_module(entry.get("module"), evidence, report["modules"])
        report["test_cases"].append({
            "id": f"TC-{len(report['test_cases']) + 1:03d}", "module": module,
            **case_details(entry),
            "priority": entry.get("priority") if entry.get("priority") in ("P0", "P1", "P2") else "P1",
            "evidence": evidence,
        })
    return report


def copy_parts(snapshot):
    review = snapshot["review"]
    title = f'{clean_text(review["title"])} · 大白话版'
    revision = f'原文版本 {snapshot["revision"]}'
    issues = [format_issue(issue) for issue in active_issues(review)]
    return title, revision, issues, review.get("flow")


def markdown(snapshot):
    title, revision, issues, flow = copy_parts(snapshot)
    review = snapshot["review"]
    source = snapshot["source_url"]
    lines = [f"# {title}", "", f"{revision} · [打开产品原文]({source})" if source else revision,
             "", clean_text(review["intro"])]
    if review.get("modules"):
        for number, module in enumerate(review["modules"], 1):
            lines.extend(["", f'## {clean_text(module["title"])}', "", clean_text(module["intro"])])
            if module["steps"]:
                lines.extend(["", "### 处理规则", ""])
                lines.extend(f"- {clean_text(step)}" for step in module["steps"])
            if module.get("flow"):
                lines.extend(["", "### 流程图", "", "```mermaid", mermaid(module["flow"]), "```"])
            active = active_issues(review, number)
            if active:
                lines.extend(["", "### 待确认", ""])
                lines.extend(f"- {format_issue(issue)}" for issue in active)
        shared = active_issues(review)
        if shared:
            lines.extend(["", "## 跨模块与未定位问题", ""])
            lines.extend(f"- {format_issue(issue)}" for issue in shared)
        if review.get("test_cases"):
            lines.extend(["", "## 测试用例", ""])
            for case in review["test_cases"]:
                module = review["modules"][case["module"] - 1]["title"] if case.get("module") else "跨模块"
                lines.extend([f'### {case["id"]} · {case["title"]}', "",
                              f'{case["priority"]} · {module}', "", f'前置条件：{case["preconditions"]}', ""])
                lines.extend(f"{index}. {step}" for index, step in enumerate(case["steps"], 1))
                lines.extend(["", f'预期：{case["expected"]}', ""])
        return "\n".join(lines) + "\n"
    lines.extend(["", "## 处理规则", ""])
    lines.extend(f"- {clean_text(step)}" for step in review["steps"])
    if flow:
        lines.extend(["", "## 流程图", "", "```mermaid", mermaid(flow), "```"])
    lines.extend(["", "## 需要产品确认的问题", ""])
    lines.extend(f"- {issue}" for issue in issues)
    if not issues:
        lines.append("本次评审未列出待确认项。")
    return "\n".join(lines) + "\n"


def active_issues(review, module=None):
    return [issue for issue in review["issues"]
            if issue["status"] != "resolved" and issue.get("module") == module]


def format_issue(issue):
    category = ISSUE_TYPES.get(issue.get("category"))
    label = f" · {category}" if category else ""
    evidence = issue.get("evidence") or []
    positions = " · 原文 " + "、".join(f'{item["line"]} 行' for item in evidence) if evidence else ""
    impact = f' 影响：{issue["impact"]}' if issue.get("impact") else ""
    return (f'{issue["id"]} · {STATUSES[issue["status"]]}{label}。'
            f'{clean_text(issue["question"])}（{clean_text(issue["source"])}）{impact}{positions}')
