import argparse
import contextlib
import http.client
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from local_env import Environment, request
from manifests import OWNER, render
from support import Handler, ThreadingHTTPServer


def environment(**overrides):
    values = dict(command="render", context="kind-kind", cluster="kind", namespace="evalhub-local",
                  connection="", tag="test", mode="mock",
                  confirm_reset="")
    values.update(overrides)
    return Environment(argparse.Namespace(**values))


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.env = environment()
        self.items = render("evalhub-local", self.env.images())["items"]

    def find(self, kind, name):
        return next(o for o in self.items if o["kind"] == kind and o["metadata"]["name"] == name)

    def test_runtime_and_callback_use_real_cluster_profile(self):
        cm = self.find("ConfigMap", "evalhub-local-config")["data"]
        config = json.loads(cm["config.yaml"])
        self.assertEqual(config["sidecar"]["eval_hub"]["base_url"],
                         "http://evalhub.evalhub-local.svc.cluster.local:8088")
        self.assertFalse(config["sidecar"]["local_mode"])
        self.assertEqual(config["sidecar"]["eval_hub"]["ca_cert_path"], "")
        self.assertIn("/data/", config["database"]["url"])
        pod = self.find("Deployment", "evalhub")["spec"]["template"]["spec"]
        self.assertEqual(pod["containers"][0]["command"], ["/app/eval-hub", "--configdir=/app/config"])
        self.assertNotIn("EVALHUB_INSTANCE_NAME", json.dumps(pod))
        provider = json.loads(cm["inspect.yaml"])
        self.assertEqual(provider["runtime"]["k8s"]["image_pull_policy"], "if_not_present")
        self.assertNotIn("local", provider["runtime"])

    def test_namespace_scope_and_no_cluster_privileges(self):
        for obj in self.items:
            self.assertEqual(obj["metadata"]["labels"], OWNER)
            if obj["kind"] != "Namespace":
                self.assertEqual(obj["metadata"]["namespace"], "evalhub-local")
            self.assertNotIn(obj["kind"], ("ClusterRole", "ClusterRoleBinding", "CustomResourceDefinition"))
        self.assertEqual(self.find("PersistentVolumeClaim", "evalhub-data")["spec"]["storageClassName"], "standard")


class GuardTests(unittest.TestCase):
    def test_api_port_discovery_handles_recreated_clusters_and_color(self):
        nodes = {"items": [{"metadata": {"name": "kind-control-plane"}, "status": {
            "nodeInfo": {"architecture": "arm64"}, "conditions": [{"type": "Ready", "status": "True"}]}}]}
        for port in (32123, 45678):
            with self.subTest(port=port):
                env = environment()
                endpoint = f"https://127.0.0.1:{port}"
                info = f"\x1b[0;32mKubernetes control plane\x1b[0m is running at \x1b[0;33m{endpoint}\x1b[0m\n"
                info += "CoreDNS is running at https://127.0.0.1:9999/api/v1/proxy\n"
                with patch.object(env, "kube", side_effect=[info, json.dumps(nodes)]) as kube:
                    self.assertEqual(env.cluster_guard(), {"kind-control-plane"})
                    self.assertEqual(env.api_server, endpoint)
                    self.assertEqual(kube.call_args_list[0].args, ("cluster-info",))

    def test_invalid_discovered_endpoint_stops_before_node_reads(self):
        env = environment()
        with patch.object(env, "kube", return_value="Kubernetes control plane is running at https://remote.example:6443") as kube:
            with self.assertRaisesRegex(RuntimeError, "loopback HTTPS"):
                env.cluster_guard()
            self.assertEqual(kube.call_count, 1)

    def test_missing_control_plane_endpoint_fails_without_fallback(self):
        env = environment()
        with patch.object(env, "kube", return_value="CoreDNS is running at https://127.0.0.1:9999/proxy") as kube:
            with self.assertRaisesRegex(RuntimeError, "Could not discover"):
                env.cluster_guard()
            self.assertEqual(kube.call_count, 1)

    def test_doctor_checks_podman_port_against_discovered_endpoint(self):
        for engine_port in ("32123", "45678"):
            with self.subTest(engine_port=engine_port):
                env = environment()
                env.api_server = "https://127.0.0.1:32123"
                node = {"Config": {"Labels": {"io.x-k8s.kind.cluster": "kind"}},
                        "NetworkSettings": {"Ports": {"6443/tcp": [{"HostPort": engine_port}]}}}
                with patch("shutil.which", return_value="/usr/bin/tool"), \
                        patch.object(env, "cluster_guard", return_value={"kind-control-plane"}), \
                        patch.object(env, "run", side_effect=["kind-control-plane", json.dumps([node])]), \
                        patch.object(env, "kube"), patch("builtins.print"):
                    if engine_port == "32123":
                        env.doctor()
                    else:
                        with self.assertRaisesRegex(RuntimeError, "discovered endpoint"):
                            env.doctor()

    def test_unowned_namespace_refused(self):
        env = environment()
        with patch.object(env, "get", return_value={"metadata": {"labels": {}}}):
            with self.assertRaisesRegex(RuntimeError, "unowned"):
                env.namespace_guard()

    def test_reset_requires_explicit_namespace_before_any_command(self):
        env = environment(command="reset")
        with patch.object(env, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "confirm-reset"):
                env.execute()
            run.assert_not_called()

    def test_down_preserves_pvc_and_namespace(self):
        env = environment(command="down")
        with patch.object(env, "cluster_guard"), patch.object(env, "namespace_guard"), patch.object(env, "kube") as kube:
            env.down()
            for call in kube.call_args_list:
                self.assertNotIn("pvc", call.args[1])
                self.assertNotIn("namespace", call.args[1])
                self.assertIn("-l", call.args)

    def test_every_kubectl_command_is_scoped(self):
        env = environment()
        with patch.object(env, "run") as run:
            env.kube("get", "pods")
            self.assertEqual(run.call_args.args[:6],
                             ("kubectl", "--context", "kind-kind", "--request-timeout=20s", "-n", "evalhub-local"))

    def test_external_url_credentials_refused(self):
        env = environment()
        with patch.dict("os.environ", {"LLM_BASE_URL": "https://user:secret@example.com/v1", "LLM_MODEL": "test"}):
            with self.assertRaisesRegex(RuntimeError, "credentials"):
                env.external_model()

    def test_render_never_contacts_cluster(self):
        env = environment()
        with patch.object(env, "run") as run, patch("builtins.print"):
            env.execute()
            run.assert_not_called()


@contextlib.contextmanager
def server(mode):
    instance = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    instance.mode = mode
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield instance.server_port
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=2)


class HTTPTests(unittest.TestCase):
    def test_all_fixtures_and_credential_free_journal(self):
        with server("mock") as port:
            base = f"http://127.0.0.1:{port}"
            request(base + "/reset", {})
            for fixture in json.loads((ROOT / "fixtures.json").read_text()):
                result = request(base + "/v1/chat/completions", {
                    "model": "local-mock", "messages": [{"role": "user", "content": fixture["prompt"]}]})
                self.assertEqual(result["choices"][0]["message"]["content"], fixture["answer"])
            journal = request(base + "/journal")
            self.assertEqual(journal["count"], 5)
            self.assertNotIn("Authorization", json.dumps(journal))
            self.assertEqual(request(base + "/reset", {})["count"], 0)

    def test_unknown_prompt_streaming_and_bad_messages_rejected(self):
        with server("mock") as port:
            for payload in ({"stream": True}, {"messages": [{"role": "user", "content": "unknown"}]},
                            {"messages": []}):
                conn = http.client.HTTPConnection("127.0.0.1", port)
                conn.request("POST", "/v1/chat/completions", json.dumps(payload), {"Content-Type": "application/json"})
                result = conn.getresponse()
                self.assertEqual(result.status, 400)
                result.read()
                conn.close()

    def test_gateway_replaces_spoofed_identity_and_preserves_callback(self):
        real_connection = http.client.HTTPConnection
        with server("gateway") as port:
            response = unittest.mock.Mock()
            response.status = 202
            response.getheader.return_value = ""
            response.getheaders.return_value = [("Content-Type", "application/json")]
            response.read.side_effect = [b'{"ok":true}', b'']
            upstream = unittest.mock.Mock()
            upstream.getresponse.return_value = response
            with patch.dict("os.environ", {"TENANT": "evalhub-local"}), \
                    patch("support.http.client.HTTPConnection", return_value=upstream):
                client = real_connection("127.0.0.1", port, timeout=5)
                client.request("POST", "/api/v1/evaluations/jobs/id/events", b'{}',
                               {"x-tenant": "other", "x-user": "admin", "Authorization": "Bearer reference"})
                result = client.getresponse()
                self.assertEqual(result.status, 202)
                self.assertEqual(json.loads(result.read()), {"ok": True})
                client.close()
            sent = upstream.request.call_args
            self.assertEqual(sent.args[:2], ("POST", "/api/v1/evaluations/jobs/id/events"))
            headers = sent.kwargs["headers"]
            self.assertEqual(headers["X-Tenant"], "evalhub-local")
            self.assertEqual(headers["X-User"], "local-developer")
            self.assertNotIn("x-tenant", headers)


if __name__ == "__main__":
    unittest.main()
