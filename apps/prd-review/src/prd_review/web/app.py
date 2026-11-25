import hashlib
from hmac import compare_digest
import json
import os
from pathlib import Path
import re
from secrets import token_urlsafe
from uuid import uuid4

from dotenv import load_dotenv
from flask import (
    Flask, Response, abort, flash, redirect, render_template, request, send_file, send_from_directory, session,
    stream_with_context, url_for,
)

from .. import review as review_rules, review_generation
from ..config import BASE_URL, MODEL, NODE_MODULES, PROJECT_ROOT, data_directory
from ..integrations.feishu import feishu_link, read_feishu_document
from ..integrations.newapi import ReviewFailure, finish
from ..store import RevisionChanged, Store
from .canvas import canvas_api, canvas_error
from .comparison import aligned_issues, aligned_lines, evidence_marks
from .xmind import export_cases


def create_app(test_config=None):
    load_dotenv(PROJECT_ROOT / ".env")
    app = Flask(__name__)
    app.config.update(
        APP_USER=os.getenv("APP_USER", "prd"),
        APP_PASSWORD=os.getenv("APP_PASSWORD", ""),
        DATA_DIR=str(data_directory()),
        CANVAS_DIST=PROJECT_ROOT / "frontend/dist",
        FEISHU_DOMAIN=os.getenv("FEISHU_DOMAIN", ""),
        REPORT_URL=os.getenv("PRD_REPORT_URL", "").rstrip("/"),
        MODEL=os.getenv("NEWAPI_MODEL", MODEL),
        MODEL_BASE_URL=os.getenv("NEWAPI_BASE_URL", BASE_URL),
        MAX_CONTENT_LENGTH=1024 * 1024,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE") == "1",
    )
    if test_config:
        app.config.update(test_config)
    password = app.config["APP_PASSWORD"]
    if len(password) < 16:
        raise RuntimeError("在服务端 .env 设置至少 16 位的 APP_PASSWORD")
    app.secret_key = hashlib.sha256(("prd-review/session/v1:" + password).encode()).digest()
    store = Store(Path(app.config["DATA_DIR"]) / "reviews.sqlite3")
    app.register_blueprint(canvas_api(store))

    @app.before_request
    def authorize():
        if request.endpoint in {"health", "static", "mermaid_asset", "canvas_asset"}:
            return
        auth = request.authorization
        if (not auth or auth.type != "basic"
                or not compare_digest((auth.username or "").encode(), app.config["APP_USER"].encode())
                or not compare_digest((auth.password or "").encode(), password.encode())):
            return Response("请登录 PRD 评审工作台。", 401,
                            {"WWW-Authenticate": 'Basic realm="PRD Review", charset="UTF-8"'})
        if "csrf" not in session:
            session["csrf"] = token_urlsafe(32)
        if request.method == "POST":
            json_api = request.blueprint == "canvas_api"
            csrf = request.headers.get("X-CSRF-Token", "") if json_api else request.form.get("csrf", "")
            if not compare_digest(csrf.encode(), session["csrf"].encode()):
                if json_api:
                    return canvas_error("INVALID_CSRF_TOKEN", "提交校验失败，请刷新后重试。", 400)
                abort(400, "提交校验失败，请刷新后重试。")

    @app.after_request
    def protect(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        if request.endpoint not in {"static", "mermaid_asset", "canvas_asset", "health"}:
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    @app.get("/assets/mermaid.min.js")
    def mermaid_asset():
        bundle = NODE_MODULES / "mermaid/dist/mermaid.min.js"
        if not bundle.is_file():
            abort(404)
        return send_file(bundle, mimetype="text/javascript", conditional=True, max_age=86400)

    @app.get("/canvas-assets/assets/<path:filename>")
    def canvas_asset(filename):
        return send_from_directory(Path(app.config["CANVAS_DIST"]) / "assets", filename, max_age=31536000)

    def canvas_bundle():
        dist = Path(app.config["CANVAS_DIST"])
        try:
            entry = json.loads((dist / ".vite/manifest.json").read_text(encoding="utf-8"))["index.html"]
        except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError):
            return None
        files = [entry["file"], *entry.get("css", [])]
        if not all(file.startswith("assets/") and (dist / file).is_file() for file in files):
            return None
        script, *styles = [url_for("canvas_asset", filename=file.removeprefix("assets/")) for file in files]
        return {"script": script, "styles": styles}

    def page(**context):
        return render_template("index.html", documents=store.all_documents(), **context)

    @app.get("/")
    def index():
        return page()

    def generate_report(source, prior, same_source=False, skill=None):
        if not source.strip():
            raise ValueError("文档正文为空。")
        if len(source.encode("utf-8")) > 180_000:
            raise ValueError("这份文档较长，请按章节分批评审。")
        payload = review_rules.review_payload(source, prior, same_source, skill)
        try:
            return (yield from review_generation.generate_review(payload, app.config["MODEL"], app.config["MODEL_BASE_URL"]))
        except ReviewFailure:
            raise
        except ValueError as exc:
            raise ValueError(f"评审未通过结构检查。{exc}") from exc
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError("评审未通过结构检查，模型返回的字段不完整。") from exc

    def uploaded_skill():
        upload = request.files.get("skill")
        if not upload or not upload.filename:
            return None
        filename = upload.filename.replace(chr(92), "/").rsplit("/", 1)[-1]
        if Path(filename).suffix.lower() not in {".md", ".markdown", ".txt"}:
            raise ValueError("请选择 Markdown 或纯文本 Skill 文件。")
        content = upload.stream.read(24_001)
        if len(content) > 24_000:
            raise ValueError("Skill 文件请控制在 24 KB 以内。")
        try:
            instructions = content.decode("utf-8-sig").replace("\r\n", "\n").strip()
        except UnicodeDecodeError as exc:
            raise ValueError("Skill 文件需使用 UTF-8 编码。") from exc
        if not instructions or chr(0) in instructions:
            raise ValueError("Skill 文件为空或包含二进制内容。")
        frontmatter = instructions.split("---", 2) if instructions.startswith("---\n") else []
        name = re.search(r"^name:\s*([^\n]+)", frontmatter[1], re.M) if len(frontmatter) == 3 else None
        label = name[1].strip().strip(chr(34) + chr(39)) if name else filename
        return {"name": label[:100] or filename[:100], "instructions": instructions}

    def respond_to_job(task, back):
        streaming = request.headers.get("Accept") == "application/x-ndjson"
        try:
            upload = uploaded_skill()
            clear = bool(request.form.get("clear_skill"))
        except ValueError as error:
            if streaming:
                return Response(json.dumps({"type": "error", "message": str(error)}, ensure_ascii=False) + "\n",
                                mimetype="application/x-ndjson")
            flash(str(error))
            return redirect(back, code=303)
        operation = task(upload, clear)
        if streaming:
            def feed():
                try:
                    destination = yield from operation
                    yield json.dumps({"type": "done", "url": destination}, ensure_ascii=False) + "\n"
                except (RevisionChanged, ValueError) as error:
                    message = "评审期间已有新版保存，请重新提交最新正文。" if isinstance(error, RevisionChanged) else str(error)
                    yield json.dumps({"type": "error", "message": message}, ensure_ascii=False) + "\n"

            # The wrapper serializes each stage as it is produced rather than waiting for the result.
            def frames():
                stream = feed()
                for event in stream:
                    yield json.dumps(event, ensure_ascii=False) + "\n" if isinstance(event, dict) else event

            return Response(stream_with_context(frames()), mimetype="application/x-ndjson",
                            headers={"X-Accel-Buffering": "no"})
        try:
            return redirect(finish(operation), code=303)
        except RevisionChanged:
            flash("评审期间已有新版保存，请重新提交最新正文。")
        except ValueError as error:
            flash(str(error))
        return redirect(back, code=303)

    @app.post("/review")
    def review():
        prior_id = request.form.get("version_id", type=int)
        pasted = request.form.get("mode") == "paste"
        back = url_for("version", version_id=prior_id) if pasted and prior_id else url_for("index")

        def operation(upload, clear):
            yield {"type": "stage", "step": "read",
                   "detail": "读取你粘贴的正文" if pasted else "从飞书只读取回文档及版本"}
            previous = None
            if pasted:
                source = request.form.get("source", "").strip()
                if prior_id:
                    selected = get_version(prior_id)
                    if selected["snapshot"].get("origin") != "paste":
                        raise ValueError("请选择一个粘贴导入的版本。")
                    doc_id = selected["document_id"]
                    previous = store.get(store.history(doc_id)[0]["id"])
                    source_url = previous["snapshot"]["source_url"]
                    revision = previous["revision"] + 1
                    if source == previous["snapshot"]["source"]:
                        if upload or clear:
                            return (yield from reformat_version(previous, upload, clear))
                        return url_for("version", version_id=previous["id"])
                else:
                    doc_id, revision = f"paste:{uuid4().hex}", 1
                    source_url = request.form.get("source_url", "").strip()
                    if source_url:
                        source_url = feishu_link(source_url, app.config["FEISHU_DOMAIN"] or None)
            else:
                doc_id, revision, source_url, source = read_feishu_document(
                    request.form.get("url", ""), app.config["FEISHU_DOMAIN"] or None,
                )
                current = store.current(doc_id, revision)
                if current:
                    if upload or clear:
                        return (yield from reformat_version(get_version(current), upload, clear))
                    return url_for("version", version_id=current)
                history = store.history(doc_id)
                previous = store.get(history[0]["id"]) if history else None
            prior = previous["snapshot"]["review"]["issues"] if previous else []
            skill = upload or (previous["snapshot"].get("skill") if previous and not clear else None)
            result = yield from generate_report(source, prior, skill=skill)
            yield {"type": "stage", "step": "save", "detail": "保存报告、测试用例和可定位的原文依据"}
            previous_revision = previous["revision"] if previous else None
            version_id = store.save({
                "doc_id": doc_id, "revision": str(revision), "source_url": source_url,
                "source": source, "origin": "paste" if pasted else "feishu",
                "previous_revision": previous_revision, "engine": app.config["MODEL"],
                "review": result, "skill": skill,
            }, previous_revision)
            return url_for("version", version_id=version_id)

        return respond_to_job(operation, back)

    def get_version(version_id):
        found = store.get(version_id)
        if not found:
            abort(404)
        return found

    def render_canvas(selected, assets):
        return render_template("canvas.html", selected=selected, assets=assets,
                               has_cases=bool(selected["snapshot"]["review"].get("test_cases")))

    @app.get("/versions/<int:version_id>/canvas")
    def canvas(version_id):
        selected = get_version(version_id)
        assets = canvas_bundle()
        if assets is None:
            return redirect(url_for("version", version_id=version_id, view="report"))
        return render_canvas(selected, assets)

    @app.get("/versions/<int:version_id>")
    def version(version_id):
        selected = get_version(version_id)
        against = request.args.get("against")
        if against == "source":
            return redirect(url_for("compare_versions", version_id=version_id, mode="source"))
        if against:
            old = selected_version(against, selected["document_id"])
            if old["revision"] >= selected["revision"]:
                abort(400, "请选择同一篇文档的较早版本。")
            return redirect(url_for("compare_versions", version_id=version_id,
                                    left=old["id"], right=version_id))
        assets = canvas_bundle()
        if request.args.get("view") != "report" and assets:
            return render_canvas(selected, assets)
        snapshot = selected["snapshot"]
        report = snapshot["review"]
        modules = [
            module | {
                "number": number,
                "issues": review_rules.active_issues(report, number),
                "diagram": review_rules.mermaid(module["flow"]) if module.get("flow") else None,
            }
            for number, module in enumerate(report.get("modules") or [], 1)
        ]
        shared = review_rules.active_issues(report)
        history = store.history(selected["document_id"])
        flow = review_rules.mermaid(report["flow"]) if not modules and report.get("flow") else None
        return page(selected=selected, history=history, report=report, statuses=review_rules.STATUSES,
                    has_canvas=bool(assets),
                    modules=modules, shared_issues=shared, issue_types=review_rules.ISSUE_TYPES,
                    has_diagrams=bool(flow or any(module["diagram"] for module in modules)), flow=flow)

    def selected_version(value, document_id):
        if not re.fullmatch(r"[1-9][0-9]{0,17}", value):
            abort(400, "对比版本格式有误。")
        version = get_version(int(value))
        if version["document_id"] != document_id:
            abort(400, "请选择同一篇文档的版本。")
        return version

    @app.get("/versions/<int:version_id>/compare")
    def compare_versions(version_id):
        selected = get_version(version_id)
        document_id = selected["document_id"]
        history = store.history(document_id)
        mode = request.args.get("mode", "source" if len(history) == 1 else "history")
        view = request.args.get("view", "copy")
        if mode not in {"history", "source"} or view not in {"copy", "original", "issues"}:
            abort(400, "对比方式无效。")
        if len(history) == 1:
            mode = "source"
        show_full = request.args.get("full") == "1"
        right_id = request.args.get("right")
        right = selected_version(right_id, document_id) if right_id is not None else selected
        rows, issues = [], []
        left_marks, right_marks = {}, {}
        if mode == "source":
            left = right
            snapshot = right["snapshot"]
            left_marks = evidence_marks(snapshot["review"])
            rows = aligned_lines(snapshot["source"], review_rules.markdown(snapshot), show_full, left_marks)
        else:
            left_id = request.args.get("left")
            if left_id is None:
                earlier = next((entry for entry in history if entry["revision"] < right["revision"]), None)
                left_id = str(earlier["id"] if earlier is not None else history[1]["id"])
                if earlier is None:
                    right = get_version(history[0]["id"])
            left = selected_version(left_id, document_id)
            if left["revision"] > right["revision"]:
                left, right = right, left
            old, new = left["snapshot"], right["snapshot"]
            if view == "issues":
                issues = aligned_issues(old["review"], new["review"], show_full)
            else:
                before, after = (old["source"], new["source"]) if view == "original" else (
                    review_rules.markdown(old), review_rules.markdown(new),
                )
                if view == "original":
                    left_marks, right_marks = evidence_marks(old["review"]), evidence_marks(new["review"])
                rows = aligned_lines(before, after, show_full, left_marks, right_marks)
        return render_template("compare.html", selected=selected, history=history, mode=mode, view=view,
                               left=left, right=right, rows=rows, issues=issues, show_full=show_full,
                               statuses=review_rules.STATUSES, left_marks=left_marks, right_marks=right_marks,
                               issue_types=review_rules.ISSUE_TYPES)

    def reformat_version(selected, upload, clear):
        snapshot = selected["snapshot"]
        review = snapshot["review"]
        yield {"type": "stage", "step": "read", "detail": "读取这版已保存的原文"}
        if not review.get("modules") or review.get("audit_version"):
            prior, same_source = review["issues"], True
        else:
            history = store.history(selected["document_id"])
            older = next((row for row in history if row["revision"] < selected["revision"]), None)
            prior = store.get(older["id"])["snapshot"]["review"]["issues"] if older else []
            same_source = False
        skill = upload or (snapshot.get("skill") if not clear else None)
        result = yield from generate_report(snapshot["source"], prior, same_source, skill)
        yield {"type": "stage", "step": "save", "detail": "保存这一版的评审结果和用例"}
        store.reformat(selected["id"], result, app.config["MODEL"], skill, review)
        return url_for("version", version_id=selected["id"])

    @app.post("/versions/<int:version_id>/reformat")
    def reformat(version_id):
        selected = get_version(version_id)
        return respond_to_job(lambda upload, clear: reformat_version(selected, upload, clear),
                              url_for("version", version_id=version_id))

    @app.get("/versions/<int:version_id>/markdown")
    def download(version_id):
        selected = get_version(version_id)
        return Response(review_rules.markdown(selected["snapshot"]), mimetype="text/markdown",
                        headers={"Content-Disposition": f'attachment; filename="review-v{selected["revision"]}.md"'})

    @app.get("/versions/<int:version_id>/xmind")
    def download_xmind(version_id):
        selected = get_version(version_id)
        review = selected["snapshot"]["review"]
        if not review.get("test_cases"):
            abort(404, "当前版本没有可导出的测试用例。")

        def source_url(line):
            base = app.config["REPORT_URL"]
            return base + url_for("compare_versions", version_id=version_id, mode="source", view="original",
                                  full="1", _anchor=f"line-left-{line}", _external=not base)

        return send_file(export_cases(review, source_url), as_attachment=True, mimetype="application/octet-stream",
                         download_name=f"test-cases-{version_id}-v{selected['revision']}.xmind")

    return app
