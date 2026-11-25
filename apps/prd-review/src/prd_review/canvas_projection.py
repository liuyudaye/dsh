"""Project an archived PRD review into the editable canvas's initial graph."""

from collections import defaultdict, deque
import re

from .review import active_issues, format_issue


COLUMN_GAP = 380
ROW_GAP = 186
MODULE_GAP = 135
NOTE_OFFSET = 18


def report_canvas(snapshot):
    """Return a deterministic initial graph without modifying the archived review."""
    review = snapshot["review"]
    modules = review.get("modules") or []
    graph = {"nodes": [_node("overview", "overview", 0, 0, review["title"], review["intro"])], "edges": []}
    bottom = ROW_GAP
    shared_issues = active_issues(review)
    if modules and shared_issues:
        _issues_note(graph, "overview", 22, bottom, shared_issues, module_id=None)
        bottom += ROW_GAP

    for number, section in enumerate(modules or [review], start=1):
        issues = active_issues(review, number) if modules else shared_issues
        lane = bottom + MODULE_GAP
        bottom = _draw_module(graph, f"module-{number}", section, issues, lane)
    return graph


def _draw_module(graph, module_id, section, issues, lane):
    graph["nodes"].append(_node(module_id, "module", 0, lane, section["title"],
                                section["intro"], module_id, section.get("anchor")))
    steps = section.get("steps") or []
    flow = section.get("flow") or {}
    if not flow.get("nodes"):
        bottom = lane + ROW_GAP
        if issues:
            _issues_note(graph, module_id, 22, bottom, issues, module_id)
            bottom += ROW_GAP
        for index, step in enumerate(steps):
            x = COLUMN_GAP * (1 + index % 2)
            y = lane + ROW_GAP * (1 + index // 2)
            heading = step.strip().splitlines()[0]
            title = heading if len(heading) <= 36 else heading[:35] + "…"
            _note(graph, f"{module_id}-rule-{index + 1}", module_id, x, y,
                  title, step if step != title else "", module_id)
            bottom = max(bottom, y + ROW_GAP)
        return bottom

    details, other_rules = _distribute_rules(steps, flow["nodes"])
    sidebar = lane + ROW_GAP
    if other_rules:
        _note(graph, f"{module_id}-rules", module_id, 22, sidebar, "模块处理规则", _bullets(other_rules), module_id)
        sidebar += ROW_GAP
    if issues:
        _issues_note(graph, module_id, 22, sidebar, issues, module_id)
        sidebar += ROW_GAP

    levels = _flow_levels(flow)
    next_row = defaultdict(lambda: lane)
    for node in flow["nodes"]:
        node_id = _flow_id(module_id, node["id"])
        level = levels[node["id"]]
        kind = "decision" if node["decision"] else "step"
        x, y = COLUMN_GAP * (level + 1), next_row[level]
        graph["nodes"].append(_node(node_id, kind, x, y, node["text"], "", module_id))
        next_row[level] += ROW_GAP
        if rules := details.get(node["id"]):
            _note(graph, f"{node_id}-detail", node_id, x + NOTE_OFFSET, next_row[level],
                  "产品细节", _bullets(rules), module_id)
            next_row[level] += ROW_GAP

    for index, edge in enumerate(flow["edges"], start=1):
        graph["edges"].append({
            "id": f"{module_id}-flow-{index}", "source": _flow_id(module_id, edge["from"]),
            "target": _flow_id(module_id, edge["to"]), "label": edge.get("label") or "", "kind": "flow",
        })
    return max(sidebar, *next_row.values())


def _flow_levels(flow):
    """Layer reachable nodes left to right and retain loop and cross edges."""
    outgoing = defaultdict(list)
    targets = set()
    ids = [node["id"] for node in flow["nodes"]]
    for edge in flow["edges"]:
        if edge["from"] != edge["to"]:
            outgoing[edge["from"]].append(edge["to"])
            targets.add(edge["to"])
    levels = {}
    roots = [node for node in ids if node not in targets]
    pending = deque(roots)
    levels.update(dict.fromkeys(roots, 0))
    # The full ID order supplies entry points for cyclic components without a root.
    for fallback in [None, *ids]:
        if fallback is not None:
            if fallback in levels:
                continue
            levels[fallback] = 0
            pending.append(fallback)
        while pending:
            source = pending.popleft()
            for target in outgoing[source]:
                if target not in levels:
                    levels[target] = levels[source] + 1
                    pending.append(target)
    return levels


def _distribute_rules(steps, nodes):
    eligible = [(node["id"], _compact(node["text"])) for node in nodes]
    details = defaultdict(list)
    others = []
    for step in steps:
        text = _compact(step)
        owners = [node_id for node_id, label in eligible if len(label) >= 3 and label in text]
        if len(owners) == 1:
            details[owners[0]].append(step)
        else:
            others.append(step)
    return details, others


def _compact(value):
    return re.sub(r"[\W_]+", "", value.casefold())


def _flow_id(module_id, node_id):
    return f"{module_id}-node-{node_id}"


def _node(node_id, kind, x, y, title, body, module_id=None, source_line=None):
    if len(title) > 500:
        title, body = title[:499] + "…", f"{title}\n\n{body}".rstrip()
    node = {"id": node_id, "kind": kind, "x": x, "y": y, "title": title, "body": body}
    if module_id is not None:
        node["module_id"] = module_id
    if type(source_line) is int and source_line > 0:
        node["source_line"] = source_line
    return node


def _issues_note(graph, owner, x, y, issues, module_id):
    evidence = issues[0].get("evidence") if len(issues) == 1 else None
    source_line = evidence[0]["line"] if evidence else None
    title = "评审问题" if module_id else "跨模块评审问题"
    _note(graph, f"{owner}-issues", owner, x, y, title,
          _bullets(format_issue(issue) for issue in issues), module_id, source_line)


def _note(graph, note_id, owner, x, y, title, body, module_id, source_line=None):
    graph["nodes"].append(_node(note_id, "note", x, y, title, body, module_id, source_line))
    graph["edges"].append({"id": f"{note_id}-link", "source": owner, "target": note_id, "label": "", "kind": "detail"})


def _bullets(items):
    return "\n".join(f"• {item}" for item in items)
