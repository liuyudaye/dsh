from copy import deepcopy
import unittest

from prd_review.canvas_projection import report_canvas


def issue(module=None):
    return {
        "id": "Q-01",
        "module": module,
        "status": "open",
        "question": "失败后能否再次提交？",
        "source": "失败分支需要产品确认",
        "evidence": [{"line": 4, "end_line": 4, "quote": "失败时显示提示"}],
    }


class CanvasProjectionTest(unittest.TestCase):
    def test_module_flow_keeps_branch_labels_and_positions_matching_rules(self):
        snapshot = {"review": {
            "title": "申请审核",
            "intro": "处理提交和审核。",
            "issues": [issue(1)],
            "modules": [{
                "title": "审核申请",
                "intro": "按权限审核。",
                "anchor": 2,
                "steps": ["审核通过后通知用户。", "操作前记录访问日志。"],
                "flow": {
                    "nodes": [
                        {"id": "N1", "text": "资料齐全", "decision": True},
                        {"id": "N2", "text": "审核通过", "decision": False},
                        {"id": "N3", "text": "退回补充", "decision": False},
                    ],
                    "edges": [
                        {"from": "N1", "to": "N2", "label": "齐全"},
                        {"from": "N1", "to": "N3", "label": "缺失"},
                    ],
                },
            }, {
                "title": "权限设置",
                "intro": "控制成员权限。",
                "steps": ["只有管理员能更改权限。"],
            }],
        }}
        original = deepcopy(snapshot)
        graph = report_canvas(snapshot)
        self.assertEqual(snapshot, original)
        self.assertEqual(graph, report_canvas(snapshot))
        nodes = {node["id"]: node for node in graph["nodes"]}
        edges = {edge["id"]: edge for edge in graph["edges"]}
        self.assertEqual(nodes["module-1"]["source_line"], 2)
        self.assertEqual(nodes["module-1-node-N1"]["kind"], "decision")
        self.assertEqual(edges["module-1-flow-2"]["label"], "缺失")
        self.assertEqual(
            (edges["module-1-flow-2"]["source"], edges["module-1-flow-2"]["target"]),
            ("module-1-node-N1", "module-1-node-N3"),
        )
        self.assertEqual(edges["module-1-node-N2-detail-link"]["source"], "module-1-node-N2")
        self.assertIn("通知用户", nodes["module-1-node-N2-detail"]["body"])
        self.assertEqual(nodes["module-1-node-N2-detail"]["module_id"], "module-1")
        self.assertIn("访问日志", nodes["module-1-rules"]["body"])
        self.assertEqual(nodes["module-1-issues"]["source_line"], 4)
        self.assertEqual(nodes["module-2-rule-1"]["kind"], "note")
        self.assertEqual(edges["module-2-rule-1-link"]["kind"], "detail")
        self.assertGreater(nodes["module-2"]["y"], max(n["y"] for n in nodes.values() if n.get("module_id") == "module-1"))

    def test_legacy_report_keeps_its_flow_loops_and_questions(self):
        snapshot = {"review": {
            "title": "旧版报告",
            "intro": "循环和独立步骤。",
            "steps": ["等待恢复"],
            "issues": [issue()],
            "flow": {
                "nodes": [
                    {"id": "N1", "text": "提交", "decision": False},
                    {"id": "N2", "text": "校验", "decision": True},
                    {"id": "N3", "text": "通知", "decision": False},
                    {"id": "N4", "text": "恢复", "decision": False},
                ],
                "edges": [
                    {"from": "N1", "to": "N2", "label": None},
                    {"from": "N2", "to": "N1", "label": "重试"},
                    {"from": "N4", "to": "N4", "label": "轮询"},
                ],
            },
        }}
        graph = report_canvas(snapshot)
        nodes = {node["id"]: node for node in graph["nodes"]}
        self.assertEqual(nodes["module-1"]["module_id"], "module-1")
        self.assertEqual(nodes["module-1-node-N3"]["kind"], "step")
        self.assertIn("再次提交", nodes["module-1-issues"]["body"])
        self.assertEqual({edge["label"] for edge in graph["edges"] if edge["kind"] == "flow"}, {"", "重试", "轮询"})
