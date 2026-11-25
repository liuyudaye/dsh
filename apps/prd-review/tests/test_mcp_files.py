from contextlib import asynccontextmanager
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import MCPError

from prd_review.store import Store


@asynccontextmanager
async def connect(**environment):
    params = StdioServerParameters(command=sys.executable, args=["-m", "prd_review.agent.mcp_server"], env=environment)
    async with stdio_client(params) as (reader, writer), ClientSession(reader, writer, read_timeout_seconds=10) as client:
        await client.initialize()
        yield client


class McpFileInputs(unittest.IsolatedAsyncioTestCase):
    async def call(self, client, name, **arguments):
        result = await client.call_tool(name, arguments)
        self.assertFalse(result.is_error, result)
        return json.loads(result.content[0].text)

    async def test_staged_review_resumes_and_saves_after_restart(self):
        with TemporaryDirectory() as directory:
            async with connect(DATA_DIR=directory) as client:
                opened = await self.call(client, "open_document", text="A requirement for operators.\nOperators are signed in.")
                document = opened["document"]
                section = {"title": "Operations", "anchor": 1, "intro": "Operators act.",
                           "steps": ["Follow the requirement."], "flow": None}
                case = {"module": "M01", "title": "Operator action", "priority": "P1",
                        "preconditions": "Operator is signed in.", "steps": ["Apply the rule."],
                        "expected": ["The action succeeds.", "A confirmation appears."],
                        "evidence": [{"line": 2, "quote": "A requirement for operators."}]}
                batch = {"document": document, "batch": 1, "modules": [section], "issues": []}
                for expected, code in (([False], "INVALID_ARGS"), ([" "], "PRD_REJECTED")):
                    rejection = await client.call_tool("add_review_batch", batch | {"test_cases": [case | {"expected": expected}]})
                    self.assertTrue(rejection.is_error)
                    self.assertEqual(rejection.meta["prdError"]["code"], code)
                    self.assertIn("expected", rejection.content[0].text)
                self.assertEqual((await self.call(client, "read_lines", document=document))["staged_batches"], [])
                await self.call(client, "add_review_batch", **batch, test_cases=[])
                await self.call(client, "add_review_batch", document=document, batch=2, issues=[], test_cases=[case])

            async with connect(DATA_DIR=directory) as client:
                self.assertEqual((await self.call(client, "read_lines", document=document))["staged_batches"], [1, 2])
                save_args = {"document": document, "title": "Operations", "intro": "Operators act."}
                archived = await self.call(client, "save_review", **save_args)
                self.assertEqual(archived["modules"], 1)
                self.assertEqual(archived["xmind_url"], archived["report_url"] + "/xmind")
                lookup = await self.call(client, "get_review", version_id=archived["version_id"])
                self.assertEqual(lookup["xmind_url"], archived["xmind_url"])
                self.assertEqual(await self.call(client, "save_review", **save_args), archived)
            review = Store(Path(directory) / "reviews.sqlite3").get(archived["version_id"])["snapshot"]["review"]
            self.assertIsNone(review["modules"][0]["anchor"])
            case, = review["test_cases"]
            self.assertEqual(case["module"], 1)
            self.assertEqual(case["expected"], "The action succeeds.；A confirmation appears.")
            self.assertEqual(case["evidence"][0]["line"], 1)

    async def test_tool_discovery_and_protocol_errors(self):
        with TemporaryDirectory() as directory:
            async with connect(DATA_DIR=directory) as client:
                tools = {tool.name: tool for tool in (await client.list_tools()).tools}
                self.assertEqual(tools["get_review"].input_schema["properties"]["version_id"]["type"], "integer")
                with self.assertRaises(MCPError) as error:
                    await client.call_tool("unknown_review_tool", {})
                self.assertEqual(error.exception.code, types.INVALID_PARAMS)
                invalid = await client.call_tool("get_review", {"version_id": "1"})
                self.assertTrue(invalid.is_error)
                self.assertEqual(invalid.meta["prdError"]["code"], "INVALID_ARGS")
                self.assertIn("version_id", invalid.content[0].text)

    async def test_workspace_and_attachment_inputs_stay_inside_their_roots(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            workspace = root / "workspace"
            workspace.mkdir()
            attachments = root / "attachments"
            attachments.mkdir()
            document = workspace / "prd.md"
            document.write_text("# PRD\nA requirement.\n", encoding="utf-8")
            attachment = attachments / "upload.txt"
            attachment.write_text("A file from chat.\n", encoding="utf-8")
            outside = root / "outside.md"
            outside.write_text("Outside the allowed roots.", encoding="utf-8")
            escape = workspace / "escape.md"
            escape.symlink_to(outside)
            async with connect(DATA_DIR=str(root / "data"), DSH_PRD_ATTACHMENTS=str(attachments),
                               DSH_PRD_WORKSPACE=str(workspace)) as client:
                for arguments, line_count in (
                    ({"workspace_path": str(document)}, 2),
                    ({"attachment_path": str(document)}, 2),
                    ({"attachment_path": str(attachment)}, 1),
                ):
                    opened = await self.call(client, "open_document", **arguments)
                    self.assertEqual(opened["line_count"], line_count)
                for arguments in ({"workspace_path": str(escape)}, {"attachment_path": str(outside)},
                                  {"workspace_path": str(attachment)}):
                    result = await client.call_tool("open_document", arguments)
                    self.assertTrue(result.is_error)
                    self.assertIn("目录内", result.content[0].text)
