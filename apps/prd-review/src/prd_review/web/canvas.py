"""Versioned report canvases over the authenticated workbench API."""

from datetime import datetime, timezone
import re

from flask import Blueprint, request
from werkzeug.exceptions import RequestEntityTooLarge

from ..canvas_projection import report_canvas


ID = re.compile(r"[A-Za-z0-9_:.-]{1,120}")
REVISION = re.compile(r"0|[1-9][0-9]{0,15}")
NODE_KINDS = {"overview", "module", "step", "decision", "note"}
EDGE_KINDS = {"detail", "flow"}
MAX_REVISION = 2**53 - 1


def identifier(value):
    return isinstance(value, str) and ID.fullmatch(value) is not None


def valid_text(value, limit):
    if not isinstance(value, str) or len(value) > limit:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def coordinate(value):
    return type(value) in (int, float) and -1_000_000 <= value <= 1_000_000


def revision_number(value):
    return type(value) is int and 0 <= value <= MAX_REVISION


def validate_graph(graph, line_count):
    """Validate editable graph fields and edge references before storing the snapshot."""
    if not isinstance(graph, dict) or graph.keys() != {"nodes", "edges"}:
        raise ValueError("画布必须包含节点和连线。")
    nodes, edges = graph["nodes"], graph["edges"]
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ValueError("节点和连线必须是列表。")
    if len(nodes) > 2_000 or len(edges) > 5_000:
        raise ValueError("画布最多允许 2000 个节点和 5000 条连线。")

    node_ids = set()
    node_fields = {"id", "kind", "x", "y", "title", "body", "source_line", "module_id"}
    for index, node in enumerate(nodes, 1):
        if (not isinstance(node, dict) or not node.keys() <= node_fields
                or not identifier(node.get("id")) or node["id"] in node_ids
                or not isinstance(node.get("kind"), str) or node["kind"] not in NODE_KINDS):
            raise ValueError(f"第 {index} 个节点的编号或类型无效。")
        if not coordinate(node.get("x")) or not coordinate(node.get("y")):
            raise ValueError(f"第 {index} 个节点的位置无效。")
        if not valid_text(node.get("title"), 500) or not valid_text(node.get("body"), 40_000):
            raise ValueError(f"第 {index} 个节点的标题或说明无效，最长分别为 500 字和 40000 字。")
        if "source_line" in node and (type(node["source_line"]) is not int
                                      or not 1 <= node["source_line"] <= line_count):
            raise ValueError(f"第 {index} 个节点引用的原文行不存在。")
        if "module_id" in node and not identifier(node["module_id"]):
            raise ValueError(f"第 {index} 个节点的模块编号无效。")
        node_ids.add(node["id"])

    edge_ids = set()
    for index, edge in enumerate(edges, 1):
        if (not isinstance(edge, dict) or not edge.keys() <= {"id", "source", "target", "label", "kind"}
                or not identifier(edge.get("id")) or edge["id"] in edge_ids
                or not valid_text(edge.get("label"), 500)
                or ("kind" in edge and (not isinstance(edge["kind"], str) or edge["kind"] not in EDGE_KINDS))):
            raise ValueError(f"第 {index} 条连线的编号、类型或标签无效。")
        if any(not identifier(edge.get(side)) or edge[side] not in node_ids for side in ("source", "target")):
            raise ValueError(f"第 {index} 条连线的起点或终点不存在。")
        edge_ids.add(edge["id"])


def canvas_error(code, message, status, **details):
    return {"error": code, "message": message, **details}, status


def canvas_api(store):
    routes = Blueprint("canvas_api", __name__)

    def find_report(version_id, connection=None):
        return store.get(version_id, connection) if 0 < version_id <= MAX_REVISION else None

    def read_version(report, history, revision, connection=None):
        if not history and revision == 0:
            return report_canvas(report["snapshot"])
        return store.canvas_graph(report["id"], revision, connection)

    def response(report, history, revision, graph):
        versions = [
            {"id": row["revision"], "created_at": row["created_at"],
             "label": (f"恢复到第 {row['restored_from']} 版" if row["restored_from"] is not None else
                       "保存画布" if row["revision"] else "解析初稿")}
            for row in history
        ]
        if not versions:
            versions.append({"id": 0, "created_at": report["created_at"], "label": "解析初稿"})
        return {"title": report["title"], "report_revision": report["revision"],
                "revision": revision, "graph": graph, "history": versions}

    @routes.get("/api/versions/<int:version_id>/canvas")
    def get_canvas(version_id):
        selected = request.args.get("revision")
        if selected is not None and (not REVISION.fullmatch(selected) or not revision_number(int(selected))):
            return canvas_error("INVALID_REVISION", "画布版本编号无效。", 400)
        report = find_report(version_id)
        if report is None:
            return canvas_error("REPORT_NOT_FOUND", "这份评审报告不存在。", 404)
        history = store.canvas_history(version_id)
        revision = int(selected) if selected is not None else history[0]["revision"] if history else 0
        graph = read_version(report, history, revision)
        if graph is None:
            return canvas_error("CANVAS_VERSION_NOT_FOUND", "这个画布版本不存在。", 404)
        return response(report, history, revision, graph)

    def update(version_id, restoring):
        try:
            payload = request.get_json(silent=True)
        except RequestEntityTooLarge:
            return canvas_error("CANVAS_TOO_LARGE", "画布大小超过服务器可接收的上限。", 413)
        data_field = "revision" if restoring else "graph"
        if (not isinstance(payload, dict) or payload.keys() != {"base_revision", data_field}
                or not revision_number(payload["base_revision"])):
            return canvas_error("INVALID_CANVAS_REQUEST", "画布保存请求格式有误。", 400)
        restored_from = payload.get("revision")
        if restoring and not revision_number(restored_from):
            return canvas_error("INVALID_REVISION", "画布版本编号无效。", 400)

        with store.transaction() as db:
            report = find_report(version_id, db)
            if report is None:
                return canvas_error("REPORT_NOT_FOUND", "这份评审报告不存在。", 404)
            history = store.canvas_history(version_id, db)
            head = history[0]["revision"] if history else 0
            if payload["base_revision"] != head:
                return canvas_error("CANVAS_CHANGED", "画布已有更新，请载入最新版本后重试。", 409, revision=head)
            current = read_version(report, history, head, db)
            if restoring:
                graph = current if restored_from == head else read_version(report, history, restored_from, db)
                if graph is None:
                    return canvas_error("CANVAS_VERSION_NOT_FOUND", "这个画布版本不存在。", 404)
                if restored_from == head:
                    return response(report, history, head, current)
            else:
                graph = payload["graph"]
                try:
                    validate_graph(graph, len(report["snapshot"]["source"].splitlines()))
                except ValueError as error:
                    return canvas_error("INVALID_GRAPH", str(error), 400)
                if graph == current:
                    return response(report, history, head, current)
            if not history:
                store.append_canvas(version_id, 0, current, report["created_at"], db)
            revision = head + 1
            store.append_canvas(version_id, revision, graph, datetime.now(timezone.utc).isoformat(), db, restored_from)
            return response(report, store.canvas_history(version_id, db), revision, graph)

    @routes.post("/api/versions/<int:version_id>/canvas")
    def save_canvas(version_id):
        return update(version_id, restoring=False)

    @routes.post("/api/versions/<int:version_id>/canvas/restore")
    def restore_canvas(version_id):
        return update(version_id, restoring=True)

    return routes
