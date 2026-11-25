from base64 import b64encode
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from prd_review.store import Store
from prd_review.web.app import create_app


REVIEW = {
    "title": "订单退款",
    "intro": "买家可以提交退款申请。",
    "modules": [{
        "title": "退款申请", "intro": "买家从订单详情发起申请。", "anchor": 1,
        "steps": ["选择退款原因。", "支付系统退回款项。"],
        "flow": {
            "nodes": [
                {"id": "N1", "text": "提交申请", "decision": False},
                {"id": "N2", "text": "是否符合退款条件", "decision": True},
            ],
            "edges": [{"from": "N1", "to": "N2", "label": "检查条件"}],
        },
    }],
    "issues": [],
    "test_cases": [],
}


class CanvasAPI(unittest.TestCase):
    def setUp(self):
        directory = Path(self.enterContext(TemporaryDirectory()))
        self.store = Store(directory / "reviews.sqlite3")
        self.original = {
            "doc_id": "test-report", "revision": "1", "source_url": "", "origin": "paste",
            "source": "退款申请\n买家选择退款原因。", "review": REVIEW,
        }
        version_id = self.store.save(self.original, None)
        self.api = f"/api/versions/{version_id}/canvas"
        password = "test-password-over-16"
        self.auth = {"Authorization": "Basic " + b64encode(f"prd:{password}".encode()).decode()}
        app = create_app({"TESTING": True, "APP_USER": "prd", "APP_PASSWORD": password,
                          "DATA_DIR": str(directory), "SESSION_COOKIE_SECURE": False})
        self.client = app.test_client()
        response = self.client.get(self.api, headers=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.seed = response.get_json()
        with self.client.session_transaction() as session:
            self.write_headers = self.auth | {"X-CSRF-Token": session["csrf"]}

    def post(self, path="", **data):
        return self.client.post(self.api + path, json=data, headers=self.write_headers)

    def test_canvas_edits_save_and_restore_without_moving_the_report_revision(self):
        self.assertEqual(self.seed["revision"], 0)
        self.assertEqual(self.seed["history"][0]["id"], 0)
        graph = deepcopy(self.seed["graph"])
        start = graph["nodes"][0]
        start.update(title="手动修改标题", x=start["x"] + 284, y=start["y"] - 96)
        graph["nodes"].append({"id": "note:1", "kind": "note", "x": 300, "y": -11, "title": "补充说明", "body": "自定义文本"})
        graph["edges"].append({"id": "new-edge", "source": start["id"], "target": "note:1", "label": "补充分支"})
        saved = self.post(base_revision=0, graph=graph)
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.get_json()["graph"], graph)
        self.assertEqual([entry["id"] for entry in saved.get_json()["history"]], [1, 0])

        stale = self.post(base_revision=0, graph=self.seed["graph"])
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.get_json()["revision"], 1)
        self.assertEqual(self.post(base_revision=1, graph=graph).get_json()["revision"], 1)
        self.assertEqual(self.store.get(1)["snapshot"], self.original)

        regenerated = REVIEW | {"intro": "重新解析后的正文概述。"}
        self.store.reformat(1, regenerated, "test", None, REVIEW)
        first = self.client.get(self.api + "?revision=0", headers=self.auth).get_json()
        self.assertEqual(first["graph"], self.seed["graph"])
        restored = self.post("/restore", base_revision=1, revision=0)
        self.assertEqual(restored.status_code, 200)
        result = restored.get_json()
        self.assertEqual(result["graph"], self.seed["graph"])
        self.assertEqual(result["report_revision"], 1)
        self.assertEqual([entry["id"] for entry in result["history"]], [2, 1, 0])
        self.assertEqual(self.store.get(1)["snapshot"]["review"], regenerated)
        prior = self.client.get(self.api + "?revision=1", headers=self.auth).get_json()
        self.assertEqual(prior["graph"], graph)
        self.assertEqual(self.post("/restore", base_revision=2, revision=200).status_code, 404)

    def test_canvas_rejects_missing_credentials_and_invalid_edits(self):
        self.assertEqual(self.client.get(self.api).status_code, 401)
        csrf = self.client.post(self.api, json={"base_revision": 0, "graph": {}}, headers=self.auth)
        self.assertEqual(csrf.status_code, 400)
        self.assertEqual(csrf.get_json()["error"], "INVALID_CSRF_TOKEN")
        graph = deepcopy(self.seed["graph"])
        graph["edges"].append({"id": "broken", "source": graph["nodes"][0]["id"], "target": "missing", "label": ""})
        invalid = self.post(base_revision=0, graph=graph)
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.get_json()["error"], "INVALID_GRAPH")
        self.assertEqual(self.client.get(self.api, headers=self.auth).get_json(), self.seed)
        self.assertEqual(self.client.get(self.api + "?revision=-1", headers=self.auth).status_code, 400)
