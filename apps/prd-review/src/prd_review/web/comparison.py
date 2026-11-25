from difflib import SequenceMatcher
from itertools import zip_longest
from typing import NamedTuple


class Line(NamedTuple):
    number: int
    text: str


class Row(NamedTuple):
    kind: str
    left: Line | None = None
    right: Line | None = None
    omitted: int = 0


def aligned_lines(before, after, show_full=False, left_marks=(), right_marks=()):
    left = before.splitlines()
    right = after.splitlines()
    matcher = SequenceMatcher(None, left, right)
    if not show_full and (left_marks or right_marks):
        return annotated_lines(left, right, matcher.get_opcodes(), left_marks, right_marks)
    groups = [matcher.get_opcodes()] if show_full else matcher.get_grouped_opcodes(3)
    rows = []
    previous_end = 0
    for group in groups:
        if not group:
            continue
        first = group[0][1]
        if first > previous_end:
            rows.append(Row("gap", omitted=first - previous_end))
        for kind, left_begin, left_end, right_begin, right_end in group:
            left_lines = (Line(i + 1, left[i]) for i in range(left_begin, left_end))
            right_lines = (Line(i + 1, right[i]) for i in range(right_begin, right_end))
            rows.extend(Row(kind, old, new) for old, new in zip_longest(left_lines, right_lines))
            previous_end = left_end
    if rows and previous_end < len(left):
        rows.append(Row("gap", omitted=len(left) - previous_end))
    return rows


def annotated_lines(left, right, opcodes, left_marks, right_marks):
    rows = []
    for index, (kind, start, stop, other_start, other_stop) in enumerate(opcodes):
        if kind != "equal":
            older = (Line(i + 1, left[i]) for i in range(start, stop))
            newer = (Line(i + 1, right[i]) for i in range(other_start, other_stop))
            rows.extend(Row(kind, old, new) for old, new in zip_longest(older, newer))
            continue
        count = stop - start
        offsets = set(range(min(3, count))) if index else set()
        if index + 1 < len(opcodes):
            offsets.update(range(max(0, count - 3), count))
        marked = [line - start - 1 for line in left_marks if start < line <= stop]
        marked.extend(line - other_start - 1 for line in right_marks if other_start < line <= other_stop)
        for offset in marked:
            offsets.update(range(max(0, offset - 1), min(count, offset + 2)))
        cursor = 0
        for offset in sorted(offsets):
            if cursor < offset:
                rows.append(Row("gap", omitted=offset - cursor))
            older = start + offset
            newer = other_start + offset
            rows.append(Row("equal", Line(older + 1, left[older]), Line(newer + 1, right[newer])))
            cursor = offset + 1
        if cursor < count:
            rows.append(Row("gap", omitted=count - cursor))
    return rows


def evidence_marks(review):
    lines = {}
    for item in review["issues"]:
        for evidence in item.get("evidence") or []:
            for line in range(evidence["line"], evidence["end_line"] + 1):
                lines.setdefault(line, set()).add(item["id"])
    return {line: sorted(issues) for line, issues in lines.items()}


def issue_context(review):
    modules = review.get("modules") or []
    return {
        item["id"]: {
            "status": item["status"], "question": item["question"], "source": item["source"],
            "category": item.get("category"), "impact": item.get("impact"),
            "evidence": item.get("evidence") or [],
            "module": modules[item["module"] - 1]["title"] if item.get("module") else None,
        }
        for item in review["issues"]
    }


def aligned_issues(before, after, show_full=False):
    left = issue_context(before)
    right = issue_context(after)
    result = []
    for issue_id in sorted(left.keys() | right.keys(), key=lambda number: int(number[2:])):
        old = left.get(issue_id)
        new = right.get(issue_id)
        if show_full or old != new:
            result.append((issue_id, old, new))
    return result
