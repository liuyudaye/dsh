from base64 import b64encode
from io import BytesIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from prd_review.integrations.newapi import ModelUnavailable
from prd_review.review import audit_report, evidence_resolver, module_outline, resolve_module, validate_review
from prd_review.store import Store
from prd_review.web.app import create_app
from prd_review.web.comparison import aligned_lines


TEST_URL = "https://example.feishu.cn/wiki/TestNode"
PASSWORD = "local-test-password-123"


def model_response(payload, model, base_url):
    revision_two = "审核" in payload["source"]
    if revision_two:
        assert payload["previous_issues"][0]["id"] == "Q-01"
    section = {
        "title": "申请退款", "anchor": None, "intro": "买家提交退款申请。",
        "steps": ["先审核。" if revision_two else "提交申请。"],
        "flow": {"nodes": [{"id": "N1", "text": "提交", "decision": False},
                           {"id": "N2", "text": "处理", "decision": False}],
                 "edges": [{"from": "N1", "to": "N2", "label": ""}]},
    }
    outline = payload.get("module_outline") or []
    modules = [section | {"anchor": entry["anchor"]} for entry in outline] or [section]
    return {"title": "退款申请", "intro": "订单退款规则。", "modules": modules,
            "issues": [{"id": "Q-01", "module": 1, "status": "resolved" if revision_two else "open",
                        "question": "是否先审核？", "source": "退款规则"}]}


def model_stream(payload, model, base_url):
    report = model_response(payload, model, base_url)
    evidence = [{"line": 1, "quote": payload["source"].splitlines()[0]}]
    report["issues"][0].update(category="conflict", impact="退款可能先于审核。", evidence=evidence)
    yield {"type": "stage", "step": "structure", "detail": "整理规则"}
    yield {"type": "progress", "step": "structure", "characters": 10}
    yield {"type": "stage", "step": "audit", "detail": "生成用例"}
    audit = {"issues": report["issues"], "test_cases": [
        {"module": 1, "title": "退款边界", "priority": "P1", "preconditions": "存在退款订单",
         "steps": ["提交申请"], "expected": "显示结果", "evidence": evidence},
    ]}
    return audit_report(report, audit, payload)


class ReviewWorkflow(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {"NEWAPI_API_KEY": "fake-test-key", "FEISHU_APP_ID": "",
                                                  "FEISHU_APP_SECRET": ""}))
        self.model = self.enterContext(patch("prd_review.review_generation.generate_review", side_effect=model_stream))
        self.app = create_app({"TESTING": True, "APP_USER": "prd", "APP_PASSWORD": PASSWORD,
                               "DATA_DIR": directory, "FEISHU_DOMAIN": "example.feishu.cn",
                               "CANVAS_DIST": Path(directory) / "frontend",
                               "SESSION_COOKIE_SECURE": False})
        self.client = self.app.test_client()
        self.auth = {"Authorization": "Basic " + b64encode(f"prd:{PASSWORD}".encode()).decode()}
        self.assertEqual(self.client.get("/", headers=self.auth).status_code, 200)
        with self.client.session_transaction() as session:
            self.csrf = session["csrf"]

    def submit(self, **fields):
        response = self.client.post("/review", data={"csrf": self.csrf, **fields}, headers=self.auth)
        self.assertEqual(response.status_code, 303)
        return response.location

    def test_report_opens_the_built_canvas_with_text_report_link(self):
        dist = self.app.config["CANVAS_DIST"]
        (dist / ".vite").mkdir(parents=True)
        (dist / "assets").mkdir()
        (dist / "assets/canvas.js").write_text("export {}", encoding="utf-8")
        (dist / "assets/canvas.css").write_text("body {}", encoding="utf-8")
        (dist / ".vite/manifest.json").write_text(json.dumps({
            "index.html": {"file": "assets/canvas.js", "css": ["assets/canvas.css"]},
        }), encoding="utf-8")
        location = self.submit(mode="paste", source="提交后退款。")
        self.assertEqual(self.client.get(location).status_code, 401)
        canvas = self.client.get(location, headers=self.auth).get_data(as_text=True)
        self.assertIn('id="prd-canvas-root"', canvas)
        self.assertIn(f'data-csrf="{self.csrf}"', canvas)
        self.assertIn(f'data-report-url="{location}?view=report"', canvas)
        self.assertIn('src="/canvas-assets/assets/canvas.js"', canvas)
        self.assertIn('href="/canvas-assets/assets/canvas.css"', canvas)
        with self.client.get("/canvas-assets/assets/canvas.js") as script:
            self.assertEqual(script.data, b"export {}")
        legacy = self.client.get(location + "?view=report", headers=self.auth).get_data(as_text=True)
        self.assertIn('class="mermaid"', legacy)
        self.assertIn(f'href="{location}/canvas"', legacy)

    def test_paste_repeat_and_compare_without_feishu_credentials(self):
        v1 = self.submit(mode="paste", source="提交后退款。", source_url=TEST_URL)
        self.assertEqual(v1, "/versions/1")
        v2 = self.submit(mode="paste", version_id=1, source="提交后先审核。")
        self.assertEqual(v2, "/versions/2")
        duplicate = self.submit(mode="paste", version_id=1, source="提交后先审核。")
        self.assertEqual(duplicate, v2)
        self.assertEqual(self.model.call_count, 2)
        page = self.client.get(v2, headers=self.auth).get_data(as_text=True)
        self.assertIn("<pre class=\"mermaid\">flowchart TD", page)
        self.assertNotIn("发布到飞书", page)
        self.assertIn(TEST_URL, page)
        self.assertIn(v2 + "/compare", page)
        legacy = self.client.get(v2 + "?against=1", headers=self.auth)
        self.assertEqual(legacy.status_code, 302)
        self.assertEqual(legacy.location, v2 + "/compare?left=1&right=2")
        original = self.client.get(legacy.location + "&view=original", headers=self.auth).get_data(as_text=True)
        self.assertIn("旧版 <span>版本 1", original)
        self.assertIn("新版 <span>版本 2", original)
        self.assertIn('class="removed evidence-cell"', original)
        self.assertIn("提交后退款。", original)
        self.assertIn('class="added evidence-cell"', original)
        self.assertIn("提交后先审核。", original)
        issues = self.client.get(legacy.location + "&view=issues", headers=self.auth).get_data(as_text=True)
        self.assertIn("Q-01", issues)
        self.assertIn('class="badge badge-open">待澄清', issues)
        self.assertIn('class="badge badge-resolved">已解决', issues)
        self.assertIn("#line-right-1", issues)
        self.assertIn('id="line-right-1"', original)
        same = self.client.get(v2 + "?against=source", headers=self.auth, follow_redirects=True).get_data(as_text=True)
        self.assertIn("产品原文 <span>版本 2", same)
        self.assertIn("大白话副本 <span>版本 2", same)
        download = self.client.get(v2 + "/markdown", headers=self.auth)
        self.assertIn("# 退款申请 · 大白话版", download.get_data(as_text=True))

    def test_feishu_reads_only_and_reuses_revision(self):
        result = {"ok": True, "data": {"document": {"document_id": "DocA", "revision_id": 3,
                                                 "content": "提交后退款。"}}}
        with (
            patch.dict(os.environ, {"FEISHU_APP_ID": "fake-id", "FEISHU_APP_SECRET": "fake-secret"}),
            patch("prd_review.integrations.feishu.tenant_access_token", return_value="fake-tenant-token") as credentials,
            patch("prd_review.integrations.feishu.subprocess.run") as cli,
        ):
            cli.return_value.returncode = 0
            cli.return_value.stdout = json.dumps(result)
            self.assertEqual(self.submit(url=TEST_URL), "/versions/1")
            self.assertEqual(self.submit(url=TEST_URL), "/versions/1")
            for call in cli.call_args_list:
                self.assertEqual(call.args[0][1:3], ["docs", "+fetch"])
                self.assertEqual(call.args[0][-2:], ["--as", "bot"])
                self.assertNotIn("NEWAPI_API_KEY", call.kwargs["env"])
                self.assertNotIn("LARKSUITE_CLI_APP_SECRET", call.kwargs["env"])
                self.assertEqual(call.kwargs["env"]["LARKSUITE_CLI_TENANT_ACCESS_TOKEN"], "fake-tenant-token")
            credentials.assert_called_with("fake-id", "fake-secret")
        self.assertEqual(self.model.call_count, 1)

    def test_xmind_download_preserves_case_structure_and_source_link(self):
        self.app.config["REPORT_URL"] = "https://reports.example.com"
        version = self.submit(mode="paste", source="提交后退款。")
        path = version + "/xmind"
        self.assertIn(f'href="{path}">下载测试用例（XMind）',
                      self.client.get(version, headers=self.auth).get_data(as_text=True))
        self.assertEqual(self.client.get(path).status_code, 401)
        with self.client.get(path, headers=self.auth) as response:
            self.assertEqual(response.status_code, 200)
            self.assertIn("test-cases-1-v1.xmind", response.headers["Content-Disposition"])
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            with ZipFile(BytesIO(response.data)) as archive:
                manifest = json.loads(archive.read("manifest.json"))
                self.assertEqual(set(manifest["file-entries"]), {"content.json", "metadata.json"})
                sheet, = json.loads(archive.read("content.json"))

        root = sheet["rootTopic"]
        self.assertEqual(root["title"], "退款申请 · 测试用例")
        module, = root["children"]["attached"]
        self.assertEqual(module["title"], "申请退款")
        case, = module["children"]["attached"]
        self.assertEqual(case["title"], "TC-001 · P1 · 退款边界")
        preconditions, steps, expected, evidence = case["children"]["attached"]
        self.assertEqual(preconditions["children"]["attached"][0]["title"], "存在退款订单")
        self.assertEqual(steps["children"]["attached"][0]["title"], "1. 提交申请")
        self.assertEqual(expected["children"]["attached"][0]["title"], "显示结果")
        citation, = evidence["children"]["attached"]
        self.assertEqual(citation["title"], "第 1 行 · 提交后退款。")
        link = version + "/compare?mode=source&view=original&full=1"
        self.assertEqual(citation["href"], "https://reports.example.com" + link + "#line-left-1")
        self.assertIn('id="line-left-1"', self.client.get(link, headers=self.auth).get_data(as_text=True))

    def test_feishu_scope_error_is_reported_without_sending_to_model(self):
        with (
            patch.dict(os.environ, {"FEISHU_APP_ID": "fake-id", "FEISHU_APP_SECRET": "fake-secret"}),
            patch("prd_review.integrations.feishu.tenant_access_token", return_value="fake-tenant-token"),
            patch("prd_review.integrations.feishu.subprocess.run") as cli,
        ):
            cli.return_value.returncode = 1
            cli.return_value.stderr = json.dumps({"error": {"code": 99991672}})
            self.assertEqual(self.submit(url=TEST_URL), "/")
        page = self.client.get("/", headers=self.auth).get_data(as_text=True)
        self.assertIn("docx:document:readonly", page)
        self.assertIn("wiki:node:read", page)
        self.model.assert_not_called()

    def test_access_and_unconfigured_feishu(self):
        self.assertEqual(self.client.get("/").status_code, 401)
        self.assertEqual(self.client.post("/review", data={"mode": "paste"}, headers=self.auth).status_code, 400)
        self.assertEqual(self.submit(url=TEST_URL), "/")
        self.assertIn("飞书只读连接尚未配置", self.client.get("/", headers=self.auth).get_data(as_text=True))
        self.assertEqual(self.client.get("/healthz").status_code, 200)

    def test_model_access_error_is_shown(self):
        self.model.side_effect = ModelUnavailable("NewAPI 当前令牌所在分组没有可用模型。")
        self.assertEqual(self.submit(mode="paste", source="退款测试正文"), "/")
        page = self.client.get("/", headers=self.auth).get_data(as_text=True)
        self.assertIn("当前令牌所在分组没有可用模型", page)

    def test_old_issue_numbers_keep_their_original_question(self):
        prior = model_response({"source": "退款申请"}, None, None)["issues"]
        revised = model_response({"source": "先审核", "previous_issues": prior}, None, None)
        revised["issues"][0]["question"] = "新版摘要"
        review = validate_review(revised, prior)
        self.assertEqual(review["issues"][0]["question"], prior[0]["question"])
        self.assertEqual(review["issues"][0]["status"], "resolved")

    def test_plain_numbered_modules_are_separate_in_page_and_markdown(self):
        source = "订单设置\n\n1. 申请入口\n1.1 功能说明\n显示按钮。\n\n2. 渠道处理\n2.1 功能说明\n发送通知。"
        self.assertEqual([item["title"] for item in module_outline(source)], ["1. 申请入口", "2. 渠道处理"])
        location = self.submit(mode="paste", source=source)
        page = self.client.get(location, headers=self.auth).get_data(as_text=True)
        self.assertIn('href="#module-1">1. 申请入口', page)
        self.assertIn('href="#module-2">2. 渠道处理', page)
        self.assertEqual(page.count('<details class="module"'), 2)
        download = self.client.get(location + "/markdown", headers=self.auth).get_data(as_text=True)
        first, second = download.split("## 2. 渠道处理")
        self.assertIn("## 1. 申请入口", first)
        self.assertIn("Q-01", first)
        self.assertNotIn("Q-01", second)

    def test_same_revision_reformat_retains_source_and_archives_legacy_review(self):
        store = Store(Path(self.app.config["DATA_DIR"]) / "reviews.sqlite3")
        original = "提交后退款。"
        legacy = {"title": "旧评审", "intro": "旧版总览。", "steps": ["提交。"], "flow": None,
                  "issues": [{"id": "Q-01", "status": "open", "question": "何时退款？", "source": "源文"}]}
        snapshot = {"doc_id": "paste:legacy", "origin": "paste", "revision": "1", "source_url": None,
                    "source": original, "review": legacy}
        version_id = store.save(snapshot, None)
        page = self.client.post(f"/versions/{version_id}/reformat", data={"csrf": self.csrf},
                                headers=self.auth, follow_redirects=True).get_data(as_text=True)
        self.assertIn("模块目录", page)
        saved = store.get(version_id)["snapshot"]
        self.assertEqual(saved["source"], original)
        self.assertEqual(saved["revision"], "1")
        self.assertEqual(saved["legacy_review"], legacy)
        self.assertEqual(saved["review"]["issues"][0]["question"], "何时退款？")

    def test_missing_source_module_is_rejected(self):
        outline = [{"title": "甲", "anchor": 3}, {"title": "乙", "anchor": 6}]
        review = model_response({"source": "测试", "module_outline": outline}, None, None)
        review["modules"].pop()
        with self.assertRaisesRegex(ValueError, "模块有遗漏"):
            validate_review(review, [], outline, require_modules=True)

    def test_compare_aligns_replacements_and_collapses_unchanged_lines(self):
        common = [f"正文 {i}" for i in range(12)]
        before = "\n".join(common + ["旧规则", "保留"])
        after = "\n".join(common + ["新规则", "补一行", "保留"])
        diff = aligned_lines(before, after)
        self.assertEqual(diff[0].omitted, 9)
        replacement, insertion = [row for row in diff if row.kind == "replace"]
        self.assertEqual((replacement.left.text, replacement.right.text), ("旧规则", "新规则"))
        self.assertIsNone(insertion.left)
        self.assertEqual(insertion.right.text, "补一行")
        self.assertNotIn("gap", [row.kind for row in aligned_lines(before, after, show_full=True)])

    def test_compare_rejects_versions_from_other_documents(self):
        first = self.submit(mode="paste", source="提交后退款。")
        self.submit(mode="paste", source="先提交资料。")
        self.assertEqual(self.client.get(first + "/compare?right=2", headers=self.auth).status_code, 400)

    def test_streamed_review_with_uploaded_skill_and_cases(self):
        result = self.client.post("/review", headers=self.auth | {"Accept": "application/x-ndjson"}, data={
            "mode": "paste", "source": "提交后退款。", "csrf": self.csrf,
            "skill": (BytesIO(b"---\nname: QA-rules\n---\nCheck boundaries."), "SKILL.md"),
        })
        frames = [json.loads(line) for line in result.get_data(as_text=True).splitlines()]
        self.assertEqual([frame["step"] for frame in frames if frame["type"] == "stage"],
                         ["read", "structure", "audit", "save"], frames)
        self.assertEqual(frames[-1], {"type": "done", "url": "/versions/1"})
        payload = self.model.call_args.args[0]
        self.assertEqual(payload["review_skill"]["name"], "QA-rules")
        page = self.client.get("/versions/1", headers=self.auth).get_data(as_text=True)
        self.assertIn("规则冲突", page)
        self.assertIn("TC-001", page)
        self.assertIn("退款边界", page)
        self.assertIn("#line-left-1", page)
        self.assertIn("测试用例", self.client.get("/versions/1/markdown", headers=self.auth).get_data(as_text=True))

    def test_source_evidence_is_verified_and_marked_even_on_an_unchanged_line(self):
        lines = [f"正文第 {i} 条" for i in range(20)]
        document = "\n".join(lines + ["旧规则"])
        verify = evidence_resolver(document)
        good = verify([{"line": 4, "quote": "正文第 2 条"}, {"line": 3, "quote": "不存在的原文"}])
        self.assertEqual(good, [{"line": 3, "end_line": 3, "quote": "正文第 2 条"}])
        updated = "\n".join(lines + ["新规则"])
        diff = aligned_lines(document, updated, left_marks={3: ["Q-01"]})
        found = next(row for row in diff if row.left and row.left.number == 3)
        self.assertEqual(found.left.text, found.right.text)

    def test_module_assignment_uses_verified_source_position(self):
        modules = [{"anchor": 10}, {"anchor": 20}, {"anchor": 30}]
        evidence = [{"line": 24, "end_line": 24}]
        self.assertEqual(resolve_module(15, evidence, modules), 2)
        self.assertIsNone(resolve_module("M02", evidence + [{"line": 36, "end_line": 37}], modules))
        self.assertEqual(resolve_module("M01", [], modules), 1)
