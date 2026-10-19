import argparse
import contextlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from godog_runner import run_godog, summarize
from local_env import Environment


class GodogTests(unittest.TestCase):
    def test_upstream_skip_errors_are_counted_as_skips(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "junit.xml"
            report.write_text('''<testsuites><testsuite>
                <testcase status="passed"/>
                <testcase status="passed"><error type="skipped"/></testcase>
                <testcase status="failed"><failure/><error type="skipped"/></testcase>
                <testcase status="undefined"><error type="undefined"/></testcase>
                <testcase><error type="infrastructure"/></testcase>
                </testsuite></testsuites>''')
            self.assertEqual(summarize(report), {"tests": 5, "passed": 1, "failed": 2, "skipped": 1, "errors": 1})

    def test_runner_executes_all_cluster_scenarios_and_preserves_failure(self):
        for exit_code in (0, 1):
            with self.subTest(exit_code=exit_code), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                repo, evidence = root / "repo", root / "reports"
                repo.mkdir()
                (repo / "tests").mkdir()
                evidence.mkdir()
                env = {"GODOG_TAGS": "@cluster", "SERVER_URL": "http://127.0.0.1:12345"}

                def process(cmd, **kwargs):
                    if cmd[0] == "go":
                        self.assertEqual(cmd[:4], ["go", "test", "-c", "-mod=readonly"])
                        self.assertEqual(kwargs["cwd"], repo)
                    else:
                        self.assertIn("--godog.tags=@cluster", cmd)
                        self.assertIn("--godog.concurrency=1", cmd)
                        self.assertIn("--godog.stop-on-failure=false", cmd)
                        self.assertEqual(kwargs["env"], env)
                        work = kwargs["cwd"]
                        self.assertEqual((work / "tests").resolve(), repo / "tests")
                        self.assertEqual((work / "bin").resolve(), evidence)
                        self.assertEqual(kwargs["timeout"], 123)
                        case = '<testcase status="failed"><failure/></testcase>' if exit_code else '<testcase status="passed"/>'
                        (evidence / "junit-godog.xml").write_text(f"<testsuites><testsuite>{case}</testsuite></testsuites>")
                    return subprocess.CompletedProcess(cmd, exit_code if cmd[0] != "go" else 0)

                with patch("godog_runner.subprocess.run", side_effect=process), patch("builtins.print"):
                    if exit_code:
                        with self.assertRaises(subprocess.CalledProcessError):
                            run_godog(repo, evidence, env, 123)
                    else:
                        run_godog(repo, evidence, env, 123)
                summary = json.loads((evidence / "godog-summary.json").read_text())
                self.assertEqual(summary["status"], "failed" if exit_code else "passed")
                self.assertFalse((repo / "bin").exists())

    def test_missing_report_cannot_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            repo, evidence = root / "repo", root / "reports"
            repo.mkdir()
            evidence.mkdir()
            with patch("godog_runner.subprocess.run", return_value=subprocess.CompletedProcess([], 0)), patch("builtins.print"):
                with self.assertRaisesRegex(RuntimeError, "nonempty scenario run"):
                    run_godog(repo, evidence, {}, 123)

    def test_remote_runner_pins_context_and_overrides_inherited_test_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "tests/features").mkdir(parents=True)
            (root / "tests/features/suite_test.go").touch()
            args = argparse.Namespace(context="kind-kind", cluster="kind", namespace="evalhub-local",
                                      connection="", evalhub_repo=root, mode="mock", godog_timeout=7200)
            with patch.dict(os.environ, {"GODOG_TAGS": "~@cluster", "GODOG_PATHS": "subset.feature",
                                          "SERVER_URL": "https://wrong-server", "AUTH_TOKEN": "private-token"}):
                env = Environment(args)
            env.state = root / "state"
            pinned = []
            config = {"contexts": [{"name": "kind-kind", "context": {"namespace": "wrong-namespace"}}]}

            def run(repo, evidence, child_env, timeout):
                self.assertEqual(child_env["GODOG_TAGS"], "@cluster")
                self.assertEqual(child_env["GODOG_PATHS"], str(root / "tests/features"))
                self.assertEqual(child_env["SERVER_URL"], "http://127.0.0.1:18088")
                self.assertEqual(child_env["METRICS_URL"], "http://127.0.0.1:18081")
                self.assertEqual(child_env["X_TENANT"], "evalhub-local")
                self.assertEqual(child_env["MODEL_AUTH_SECRET_REF"], "local-mock-llm")
                self.assertEqual(child_env["AUTH_TOKEN"], "local-kind-development")
                path = Path(child_env["KUBECONFIG"])
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                data = json.loads(path.read_text())
                self.assertEqual(data["current-context"], "kind-kind")
                self.assertEqual(data["contexts"][0]["context"]["namespace"], "evalhub-local")
                self.assertNotIn(evidence, path.parents)
                pinned.append(path)

            with patch.object(env, "cluster_guard"), patch.object(env, "namespace_guard"), \
                    patch("shutil.which", return_value="tool"), patch.object(env, "get", return_value={"data": {}}), \
                    patch.object(env, "forwarded", side_effect=lambda svc, port: contextlib.nullcontext(f"http://127.0.0.1:{port + 10000}")), \
                    patch("local_env.request"), patch.object(env, "kube", return_value=json.dumps(config)), \
                    patch.object(env, "run", side_effect=["revision", ""]), patch.object(env, "collect") as collect, \
                    patch("godog_runner.run_godog", side_effect=run):
                env.godog()
                collect.assert_called_once()
            self.assertFalse(pinned[0].exists())


if __name__ == "__main__":
    unittest.main()
