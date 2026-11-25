import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from ruamel.yaml import YAML

from prd_review.agent import launcher


class AgentLauncherTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory())).resolve()
        self.home = self.root / "dsh"
        self.profile = self.home / "profiles" / "web" / "cordis.patch.yml"
        for filename in ("apps/cli/src/bin.ts", "apps/web/dist/index.html"):
            file = self.root / filename
            file.parent.mkdir(parents=True, exist_ok=True)
            file.touch()

    def launch(self, *args, key="fixture-key", model="fixture-model"):
        env = {"DSH_HOME": str(self.home), "NEWAPI_MODEL": model}
        if key:
            env["NEWAPI_API_KEY"] = key
        with (
            patch.multiple(launcher, PROJECT_ROOT=self.root, DSH_SOURCE_ROOT=self.root),
            patch.dict(os.environ, env, clear=True),
            patch.object(launcher.sys, "argv", ["prd-review-agent", *args]),
            patch.object(launcher.shutil, "which", return_value="/fixture/corepack"),
            patch.object(launcher.os, "chdir"),
            patch.object(launcher.os, "execve") as execute,
        ):
            launcher.main()
            return execute.call_args.args[1]

    def test_web_keeps_model_updates_after_subsequent_launches(self):
        self.profile.parent.mkdir(parents=True)
        self.profile.write_text("# Keep this setting.\n- id: other\n  config: {path: !!js process.cwd()}\n")
        command = self.launch()
        self.assertNotIn(str(self.home / "newapi.agent.yml"), command)
        written = self.profile.read_text()
        self.assertIn("# Keep this setting.", written)
        self.assertIn("!!js process.cwd()", written)
        config = {row["id"]: row["config"] for row in YAML(typ="rt").load(written)}
        self.assertEqual(config["llm-pi-ai"]["providers"]["newapi"]["models"][0]["id"], "fixture-model")
        self.assertEqual(config["agent-default-model"]["model"], "fixture-model")
        self.assertNotIn("fixture-key", written)

        saved = "- id: llm-pi-ai\n  config: {providers: {}}\n"
        self.profile.write_text(saved)
        self.launch(model="another-model")
        self.assertEqual(self.profile.read_text(), saved)

    def test_existing_model_configuration_precedes_bootstrap(self):
        self.profile.parent.mkdir(parents=True)
        existing = "- id: llm-pi-ai\n  config: {providers: {local: {}}}\n"
        self.profile.write_text(existing)
        self.launch()
        self.assertEqual(self.profile.read_text(), existing)

    def test_web_can_start_before_a_key_is_configured(self):
        command = self.launch(key=None)
        self.assertFalse(self.profile.exists())
        self.assertNotIn(str(self.home / "newapi.agent.yml"), command)

    def test_headless_loads_its_explicit_model_patch(self):
        command = self.launch("--headless", "fixture prompt")
        overlay = self.home / "newapi.agent.yml"
        self.assertIn(str(overlay), command)
        operations = json.loads(overlay.read_text())
        self.assertEqual(operations[1]["config"]["model"], "fixture-model")
        self.assertFalse(self.profile.exists())
