"""Command-line operations on individual JSON snapshots."""

import argparse
import difflib
import json
import os
from pathlib import Path
import subprocess
import tempfile

from .config import MODEL, BASE_URL
from .integrations.feishu import fetch_doc, feishu_link
from .integrations.newapi import finish
from .review import STATUSES, markdown, review_payload, validate_review
from .review_generation import generate_review


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def source_doc(args):
    if args.file:
        if not args.doc_id or not args.revision:
            raise ValueError("读取本地原文时请指定 --doc-id 和 --revision")
        return args.doc_id, args.revision, None, args.file.read_text(encoding="utf-8")
    return fetch_doc(feishu_link(args.feishu))


def review_doc(args):
    doc_id, revision, source_url, source = source_doc(args)
    previous = read_json(args.previous) if args.previous else None
    if args.out.exists():
        raise ValueError("目标快照已存在，请使用新的文件名")
    if previous and previous["doc_id"] != doc_id:
        raise ValueError("上一份快照属于另一篇文档")
    if previous and previous["revision"] == revision:
        raise ValueError("原文版本相同，直接使用已有快照")
    if not source.strip():
        raise ValueError("原文为空")
    prior = previous["review"]["issues"] if previous else []
    payload = review_payload(source, prior)
    review = (validate_review(read_json(args.fixture), prior, payload["module_outline"]) if args.fixture
              else finish(generate_review(payload, args.model, args.base_url)))
    snapshot = {
        "doc_id": doc_id,
        "revision": revision,
        "previous_revision": previous["revision"] if previous else None,
        "source_url": source_url,
        "source": source,
        "engine": "fixture" if args.fixture else args.model,
        "review": review,
    }
    # Publish the complete snapshot atomically and preserve any earlier snapshot.
    with tempfile.TemporaryDirectory(dir=args.out.parent) as directory:
        pending = Path(directory) / "snapshot.json"
        pending.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.link(pending, args.out)


def print_diff(left, right, before, after):
    print("\n".join(difflib.unified_diff(left.splitlines(), right.splitlines(),
                                        before, after, lineterm="")))


def compare(old, new=None):
    if new is None:
        revision = old["revision"]
        print_diff(old["source"], markdown(old), f"{revision}/产品原文", f"{revision}/大白话副本")
        return
    if old["doc_id"] != new["doc_id"]:
        raise ValueError("只能比较同一篇原文的快照")
    before, after = old["revision"], new["revision"]
    for name, left, right in (
        ("产品原文", old["source"], new["source"]),
        ("大白话副本", markdown(old), markdown(new)),
    ):
        print(f"\n# {name}")
        print_diff(left, right, f"{before}/{name}", f"{after}/{name}")
    previous = {issue["id"]: issue for issue in old["review"]["issues"]}
    current = {issue["id"]: issue for issue in new["review"]["issues"]}
    print("\n# 问题状态")
    for issue in current.values():
        label = STATUSES[issue["status"]]
        if issue["id"] not in previous:
            label = f"新增 / {label}"
        print(f'- {issue["id"]} · {label} · {issue["question"]}（{issue["source"]}）')
    for issue_id in previous.keys() - current.keys():
        print(f"- {issue_id} · 新快照未记录，状态未核验")


def main():
    parser = argparse.ArgumentParser(description="飞书 PRD 大白话副本与版本对比")
    actions = parser.add_subparsers(dest="action", required=True)
    review = actions.add_parser("review", help="从本地文件或飞书链接生成版本快照")
    source = review.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", type=Path)
    source.add_argument("--feishu")
    review.add_argument("--doc-id")
    review.add_argument("--revision")
    review.add_argument("--previous", type=Path)
    review.add_argument("--fixture", type=Path, help="使用指定的离线评审结果")
    review.add_argument("--model", default=os.getenv("NEWAPI_MODEL", MODEL))
    review.add_argument("--base-url", default=os.getenv("NEWAPI_BASE_URL", BASE_URL))
    review.add_argument("--out", required=True, type=Path)
    copy = actions.add_parser("copy")
    copy.add_argument("snapshot", type=Path)
    diff = actions.add_parser("diff")
    diff.add_argument("old", type=Path)
    diff.add_argument("new", type=Path, nargs="?", help="省略时对比当版的产品原文和副本")
    args = parser.parse_args()
    try:
        if args.action == "review":
            review_doc(args)
        elif args.action == "diff":
            compare(read_json(args.old), read_json(args.new) if args.new else None)
        elif args.action == "copy":
            snapshot = read_json(args.snapshot)
            print(markdown(snapshot), end="")
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ImportError, subprocess.TimeoutExpired) as exc:
        parser.exit(1, f"处理失败。{exc}\n")


if __name__ == "__main__":
    main()
