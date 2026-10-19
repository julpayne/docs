#!/usr/bin/env python3
"""Scoped build/deploy commands for the existing Podman-backed kind cluster."""
import argparse
import base64
import contextlib
import datetime
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from manifests import OWNER, RULES, SELECTOR, render

ROOT = Path(__file__).resolve().parent
CONTRIB_REVISION = "24e92727de1948355dcd4c5ba0752b97e6e3121f"
JOB_SELECTOR = "app=evalhub,component=evaluation-job"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def request(url, data=None, method=None):
    payload = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(url, data=payload, method=method,
                                 headers={"Content-Type": "application/json"})
    # Cluster port-forwards must not be routed through a configured HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=15) as response:
        return json.load(response)


class Environment:
    def __init__(self, args):
        self.args = args
        self.env = dict(os.environ, KIND_EXPERIMENTAL_PROVIDER="podman")
        if args.connection:
            self.env["CONTAINER_CONNECTION"] = args.connection
        self.state = ROOT / "bin" / f"{args.context}-{args.namespace}"
        self.redactions = []
        self.api_server = None

    def run(self, *cmd, capture=False, input=None, timeout=60):
        result = subprocess.run([str(x) for x in cmd], env=self.env, input=input,
                                text=True, stdout=subprocess.PIPE if capture else None,
                                check=True, timeout=timeout)
        return result.stdout.strip() if capture else ""

    def kube(self, *args, **kwargs):
        return self.run("kubectl", "--context", self.args.context,
                        "--request-timeout=20s", "-n", self.args.namespace, *args, **kwargs)

    def get(self, kind, name):
        value = self.kube("get", kind, name, "--ignore-not-found", "-o", "json", capture=True)
        return json.loads(value) if value else None

    def cluster_guard(self):
        a = self.args
        require(a.context == f"kind-{a.cluster}", "Context must match kind-<cluster>.")
        info = self.kube("cluster-info", capture=True)
        info = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", info)
        endpoints = re.findall(r"^Kubernetes control plane is running at (\S+)\s*$", info, re.MULTILINE)
        require(len(endpoints) == 1, "Could not discover one API endpoint from kubectl cluster-info.")
        parsed = urllib.parse.urlsplit(endpoints[0])
        require(parsed.scheme == "https" and parsed.hostname in ("127.0.0.1", "localhost") and parsed.port,
                "Discovered API server must be a loopback HTTPS endpoint with a port.")
        self.api_server = endpoints[0]
        nodes = json.loads(self.kube("get", "nodes", "-o", "json", capture=True))["items"]
        require(nodes, "Cluster has no nodes.")
        for node in nodes:
            require(node["status"]["nodeInfo"]["architecture"] == "arm64", "Expected ARM64 nodes.")
            require(any(c["type"] == "Ready" and c["status"] == "True"
                        for c in node["status"]["conditions"]), f"Node {node['metadata']['name']} is not Ready.")
        return {node["metadata"]["name"] for node in nodes}

    def doctor(self):
        for command in ("podman", "kind", "kubectl", "git"):
            require(shutil.which(command), f"Missing executable: {command}")
        nodes = self.cluster_guard()
        engine_nodes = set(self.run("kind", "get", "nodes", "--name", self.args.cluster,
                                    capture=True).splitlines())
        require(nodes == engine_nodes, "Selected Podman connection does not own these kind nodes.")
        ports = []
        for node in sorted(nodes):
            info = json.loads(self.run("podman", "container", "inspect", node, capture=True))[0]
            require(info["Config"]["Labels"].get("io.x-k8s.kind.cluster") == self.args.cluster,
                    "Podman node is not owned by the selected kind cluster.")
            ports.extend(p["HostPort"] for p in
                         (info["NetworkSettings"].get("Ports") or {}).get("6443/tcp", []) or [])
        require(str(urllib.parse.urlsplit(self.api_server).port) in ports,
                "Podman node API port does not match the discovered endpoint; check --connection.")
        self.kube("get", "storageclass", "standard")
        print(f"Ready: {self.args.context} ({self.api_server}), ARM64, namespace {self.args.namespace}", file=sys.stderr)

    def owned(self, obj):
        actual = self.get(obj["kind"], obj["metadata"]["name"])
        if actual:
            require(all(actual["metadata"].get("labels", {}).get(k) == v for k, v in OWNER.items()),
                    f"Refusing to modify unowned {obj['kind']}/{obj['metadata']['name']}.")
        return actual

    def namespace_guard(self):
        require(self.owned({"kind": "Namespace", "metadata": {"name": self.args.namespace}}),
                "Environment namespace does not exist; run up first.")

    def images(self):
        if self.args.tag:
            return {key: f"localhost/evalhub-local-{key}:{self.args.tag}"
                    for key in ("evalhub", "inspect", "support")}
        path = self.state / "images.json"
        require(path.is_file(), "No build record; run build-load or pass --tag for a preview render.")
        return json.loads(path.read_text())["images"]

    def build_load(self):
        self.doctor()
        a = self.args
        require((a.evalhub_repo / "Containerfile").is_file(), "EvalHub checkout/Containerfile missing.")
        adapter = a.contrib_repo / "adapters" / "inspect"
        require((adapter / "Containerfile").is_file(), "Contrib Inspect adapter checkout missing.")
        contrib_sha = self.run("git", "-C", a.contrib_repo, "rev-parse", "HEAD", capture=True)
        require(contrib_sha == a.contrib_revision, "Contrib revision differs from --contrib-revision.")
        require(not self.run("git", "-C", a.contrib_repo, "status", "--porcelain", "--", "adapters/inspect",
                             capture=True), "Inspect adapter has local changes; use a clean pinned checkout.")
        core_sha = self.run("git", "-C", a.evalhub_repo, "rev-parse", "HEAD", capture=True)
        a.tag = a.tag or f"{core_sha[:10]}-{datetime.datetime.now(datetime.timezone.utc):%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"
        images = self.images()
        self.state.mkdir(parents=True, exist_ok=True)
        for image in images.values():
            exists = subprocess.run(["podman", "image", "exists", image], env=self.env, timeout=30)
            require(exists.returncode == 1, f"Tag already exists (or Podman failed): {image}; choose a new tag.")
        print(f"Building tag {a.tag}; Go requirement: " + next(
            line for line in (a.evalhub_repo / "go.mod").read_text().splitlines() if line.startswith("go ")),
            file=sys.stderr)
        common = ["podman", "build", "--platform=linux/arm64"]
        self.run(*common, "--build-arg", "TARGETARCH=arm64", "--build-arg", "BUILDPLATFORM=linux/arm64",
                 "--build-arg", f"GIT_HASH={core_sha}", "-t", images["evalhub"],
                 "-f", a.evalhub_repo / "Containerfile", a.evalhub_repo, timeout=3600)
        # Stage upstream files so the source checkouts remain untouched. Upstream COPY
        # requires nltk-data-airgap/ even in connected mode; retain a placeholder.
        with tempfile.TemporaryDirectory(prefix="inspect-build-") as tmp:
            stage = Path(tmp)
            shutil.copytree(adapter, stage, dirs_exist_ok=True)
            (stage / "nltk-data-airgap").mkdir(exist_ok=True)
            (stage / "nltk-data-airgap" / ".gitkeep").touch()
            (stage / "tasks").mkdir(exist_ok=True)
            for name in ("local_smoke.py", "fixtures.json"):
                shutil.copy2(ROOT / name, stage / "tasks" / name)
            with (stage / "Containerfile").open("a") as out:
                out.write("\nCOPY --chown=1001:0 tasks/ /app/tasks/\n")
            self.run(*common, "-t", images["inspect"], "-f", stage / "Containerfile", stage, timeout=3600)
        self.run("podman", "run", "--rm", "--network=none", "--entrypoint=python", images["inspect"],
                 "-c", "from tasks.local_smoke import local_smoke; assert len(local_smoke().dataset) == 5",
                 timeout=60)
        self.run(*common, "-t", images["support"], "-f", ROOT / "Containerfile.support", ROOT, timeout=600)
        ids = {}
        for key, image in images.items():
            info = json.loads(self.run("podman", "image", "inspect", image, capture=True))[0]
            require(info["Architecture"] == "arm64", f"Incorrect image architecture: {image}")
            ids[key] = info["Id"]
            with tempfile.TemporaryDirectory(prefix="kind-image-") as tmp:
                archive = Path(tmp) / "image.tar"
                self.run("podman", "save", "--format", "oci-archive", "-o", archive, image, timeout=600)
                self.run("kind", "load", "image-archive", "--name", a.cluster, archive, timeout=600)
        record = {"images": images, "ids": ids, "evalhub_sha": core_sha, "contrib_sha": contrib_sha,
                  "evalhub_dirty": bool(self.run("git", "-C", a.evalhub_repo, "status", "--porcelain", capture=True)),
                  "context": a.context, "api_server": self.api_server}
        (self.state / "images.json").write_text(json.dumps(record, indent=2) + "\n")
        print(f"Built and loaded images. Record: {self.state / 'images.json'}")

    def verify_loaded(self):
        for node in self.run("kind", "get", "nodes", "--name", self.args.cluster, capture=True).splitlines():
            loaded = set(self.run("podman", "exec", node, "ctr", "--namespace=k8s.io", "images", "ls", "-q",
                                  capture=True).splitlines())
            for image in self.images().values():
                require(image in loaded, f"Image missing from {node}: {image}; run build-load.")

    def up(self):
        self.doctor()
        self.verify_loaded()
        manifest = render(self.args.namespace, self.images())
        # Check namespace ownership before reading or writing its resources.
        namespace = self.owned(manifest["items"][0])
        if namespace:
            for obj in manifest["items"][1:]:
                self.owned(obj)
        if self.args.mode == "external":
            require(namespace, "Run up in mock mode, then secret, before selecting external mode.")
            self.external_model()
        self.state.mkdir(parents=True, exist_ok=True)
        rendered = json.dumps(manifest, indent=2) + "\n"
        (self.state / "manifest.json").write_text(rendered)
        self.kube("apply", "-f", "-", input=rendered)
        for rule in RULES:
            group = rule["apiGroups"][0]
            for resource in rule["resources"]:
                for verb in rule["verbs"]:
                    base, _, sub = resource.partition("/")
                    target = base + ("." + group if group else "")
                    flags = ["--subresource", sub] if sub else []
                    require(self.kube("auth", "can-i", verb, target, *flags,
                                      "--as", f"system:serviceaccount:{self.args.namespace}:evalhub",
                                      capture=True) == "yes", f"Missing RBAC: {verb} {resource}")
        for name in ("evalhub", "mock-llm"):
            self.kube("rollout", "status", f"deployment/{name}", "--timeout=180s", timeout=200)
        with self.forwarded("evalhub", 8088) as base:
            request(base + "/api/v1/health")
            providers = request(base + "/api/v1/evaluations/providers")
            require(any(p["resource"]["id"] == "inspect" for p in providers["items"]),
                    "Inspect provider was not discovered.")
        print("Environment ready. Run local-env.sh forward or local-env.sh smoke.")

    @contextlib.contextmanager
    def forwarded(self, service, remote):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        with tempfile.TemporaryFile(mode="w+") as log:
            process = subprocess.Popen(["kubectl", "--context", self.args.context, "-n", self.args.namespace,
                "port-forward", "--address=127.0.0.1", f"svc/{service}", f"{port}:{remote}"],
                env=self.env, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    require(process.poll() is None, "Port-forward exited before becoming ready.")
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                            break
                    except OSError:
                        time.sleep(0.2)
                else:
                    raise RuntimeError("Port-forward startup deadline exceeded.")
                yield f"http://127.0.0.1:{port}"
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()

    def external_model(self):
        url, model = os.getenv("LLM_BASE_URL", ""), os.getenv("LLM_MODEL", "")
        parsed = urllib.parse.urlsplit(url)
        require(parsed.scheme in ("http", "https") and parsed.hostname and model,
                "External mode requires LLM_BASE_URL and LLM_MODEL.")
        require(not parsed.username and not parsed.password and not parsed.query and not parsed.fragment,
                "LLM_BASE_URL must not contain credentials, query parameters, or fragments.")
        require(not re.search(r"/v1/v1(?:/|$)", parsed.path), "LLM_BASE_URL contains duplicate /v1.")
        secret = self.get("secret", "external-llm")
        require(secret and secret.get("data", {}).get("api-key"), "Create external-llm with an api-key first.")
        self.redactions.append(base64.b64decode(secret["data"]["api-key"]).decode())
        return {"name": model, "url": url.rstrip("/"), "auth": {"secret_ref": "external-llm"}}

    def secret(self):
        self.cluster_guard()
        self.namespace_guard()
        path = self.args.key_file
        require(path and path.is_file(), "Set LLM_API_KEY_FILE or --key-file to a file containing only the key.")
        if ROOT in path.resolve().parents:
            require(ROOT / "secrets" in path.resolve().parents,
                    "Credential files inside this project must be under ignored secrets/.")
        value = path.read_bytes().strip()
        require(value, "Credential file is empty.")
        obj = {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
               "metadata": {"name": "external-llm", "namespace": self.args.namespace, "labels": OWNER},
               "data": {"api-key": base64.b64encode(value).decode()}}
        self.owned(obj)
        # Server-side apply avoids duplicating the key in a last-applied annotation.
        self.kube("apply", "--server-side", "--field-manager=evalhub-local-kind", "-f", "-",
                  input=json.dumps(obj))

    def godog(self):
        from godog_runner import run_godog

        self.cluster_guard()
        self.namespace_guard()
        for command in ("go", "oc"):
            require(shutil.which(command), f"Godog @cluster requires {command} on the host.")
        repo = self.args.evalhub_repo.resolve()
        require((repo / "tests/features/suite_test.go").is_file(), "EvalHub tests/features checkout missing.")
        if self.args.mode == "external":
            model = self.external_model()
        else:
            model = {"name": "local-mock", "url": f"http://mock-llm.{self.args.namespace}.svc.cluster.local:8000/v1",
                     "auth": {"secret_ref": "local-mock-llm"}}
            require(self.get("secret", "local-mock-llm"), "Mock credential fixture missing; run up first.")
        run_id = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ-godog-") + uuid.uuid4().hex[:8]
        evidence = self.state / "runs" / run_id
        evidence.mkdir(parents=True)
        # Keep raw kubeconfig credentials private and out of the evidence directory.
        try:
            with tempfile.TemporaryDirectory(prefix="evalhub-godog-kubeconfig-") as tmp, \
                    self.forwarded("evalhub", 8088) as base, self.forwarded("evalhub", 8081) as metrics:
                request(base + "/api/v1/health")
                config = json.loads(self.kube("config", "view", "--minify", "--flatten", "--raw", "-o", "json",
                                              capture=True))
                config["current-context"] = self.args.context
                for context in config["contexts"]:
                    context["context"]["namespace"] = self.args.namespace
                kubeconfig = Path(tmp) / "config"
                kubeconfig.touch(mode=0o600)
                kubeconfig.write_text(json.dumps(config))
                env = dict(self.env, SERVER_URL=base, METRICS_URL=metrics, GODOG_TAGS="@cluster",
                           GODOG_PATHS=str(repo / "tests/features"), KUBECONFIG=str(kubeconfig),
                           X_TENANT=self.args.namespace, X_USER="local-developer",
                           KUBERNETES_NAMESPACE=self.args.namespace, AUTH_TOKEN="local-kind-development",
                           MODEL_URL=model["url"], MODEL_NAME=model["name"],
                           MODEL_AUTH_SECRET_REF=model["auth"]["secret_ref"])
                env.setdefault("GOTOOLCHAIN", "auto")
                env.setdefault("WAIT_DEADLINE", "5m")
                metadata = {"tags": "@cluster", "mode": self.args.mode, "context": self.args.context,
                            "namespace": self.args.namespace, "api_server": self.api_server,
                            "source_sha": self.run("git", "-C", repo, "rev-parse", "HEAD", capture=True),
                            "source_dirty": bool(self.run("git", "-C", repo, "status", "--porcelain", capture=True))}
                (evidence / "godog-run.json").write_text(json.dumps(metadata, indent=2) + "\n")
                run_godog(repo, evidence, env, self.args.godog_timeout)
        finally:
            self.collect(evidence, None)

    def smoke(self):
        self.cluster_guard()
        self.namespace_guard()
        model = self.external_model() if self.args.mode == "external" else {
            "name": "local-mock", "url": f"http://mock-llm.{self.args.namespace}.svc.cluster.local:8000/v1"}
        self.state.mkdir(parents=True, exist_ok=True)
        run_id = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
        evidence = self.state / "runs" / run_id
        evidence.mkdir(parents=True)
        payload = {"name": f"local-{self.args.mode}-{run_id}", "model": model, "benchmarks": [{
            "id": "inspect/custom", "provider_id": "inspect",
            "primary_score": {"metric": "exact/mean", "lower_is_better": False},
            "pass_criteria": {"threshold": 1.0},
            "parameters": {"task": "/app/tasks/local_smoke.py@local_smoke", "num_examples": 5,
                           "sandbox": "none", "max_tasks": 1,
                           "model_args": {"streaming": False, "responses_api": False, "client_timeout": 30}}}]}
        (evidence / "request.json").write_text(json.dumps(payload, indent=2))
        (evidence / "mode.txt").write_text(self.args.mode + "\n")
        job_id = None
        try:
            with self.forwarded("evalhub", 8088) as base, contextlib.ExitStack() as stack:
                mock = stack.enter_context(self.forwarded("mock-llm", 8000)) if self.args.mode == "mock" else None
                if mock:
                    request(mock + "/reset", {}, "POST")
                response = request(base + "/api/v1/evaluations/jobs", payload)
                job_id = response["resource"]["id"]
                (evidence / "job-id.txt").write_text(job_id + "\n")
                deadline = time.monotonic() + 600
                while time.monotonic() < deadline:
                    response = request(base + f"/api/v1/evaluations/jobs/{job_id}")
                    (evidence / "response.json").write_text(json.dumps(response, indent=2))
                    state = response.get("status", {}).get("state")
                    with (evidence / "history.jsonl").open("a") as out:
                        out.write(json.dumps({"time": time.time(), "state": state}) + "\n")
                    if state == "completed":
                        break
                    require(state not in ("failed", "cancelled", "partially_failed"), f"API job ended {state}.")
                    time.sleep(2)
                else:
                    raise RuntimeError("Evaluation exceeded ten-minute polling deadline; inspect saved evidence.")
                jobs = json.loads(self.kube("get", "jobs", "-l", f"job_id={job_id}", "-o", "json", capture=True))
                require(len(jobs["items"]) == 1, "Expected one backing Kubernetes Job.")
                self.kube("wait", "--for=condition=complete", "job", "-l", f"job_id={job_id}",
                          "--timeout=60s", timeout=80)
                benchmarks = response.get("results", {}).get("benchmarks", [])
                require(len(benchmarks) == 1 and benchmarks[0].get("metrics"), "Missing benchmark metrics.")
                require(benchmarks[0].get("additional_info", {}).get("num_samples_effective") == 5,
                        "Inspect did not report five evaluated samples.")
                if mock:
                    journal = request(mock + "/journal")
                    (evidence / "journal.json").write_text(json.dumps(journal, indent=2))
                    fixtures = json.loads((ROOT / "fixtures.json").read_text())
                    require(journal["count"] == 5 and
                            sorted(r["prompt"] for r in journal["requests"]) == sorted(r["prompt"] for r in fixtures),
                            "Expected exactly five fixture requests; run only one evaluation at a time.")
                    require(benchmarks[0].get("test", {}).get("primary_score") == 1.0 and
                            response.get("results", {}).get("test", {}).get("score") == 1.0,
                            "Expected benchmark and aggregate scores of 1.0.")
                # The API log endpoint is streaming/text; save it without JSON decoding.
                with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                        base + f"/api/v1/evaluations/jobs/{job_id}/logs", timeout=20) as logs:
                    (evidence / "api-job.log").write_bytes(logs.read())
            print(f"Smoke passed ({self.args.mode}). Evidence: {evidence}")
        finally:
            self.collect(evidence, job_id)

    def collect(self, evidence, job_id):
        for resource in ("jobs", "pods", "configmaps", "events"):
            args = ["get", resource, "-o", "json"]
            if job_id and resource != "events":
                args += ["-l", f"job_id={job_id}"]
            # Never collect Secret data or credential-bearing environment variables.
            try:
                (evidence / f"{resource}.json").write_text(self.kube(*args, capture=True))
            except (subprocess.SubprocessError, OSError) as error:
                print(f"Evidence collection failed for {resource}: {error}", file=sys.stderr)
        for name in ("evalhub", "mock-llm"):
            try:
                (evidence / f"{name}.log").write_text(self.kube("logs", f"deployment/{name}",
                    "--all-containers=true", "--tail=500", capture=True))
            except subprocess.SubprocessError:
                pass
        if job_id:
            try:
                (evidence / "job-containers.log").write_text(self.kube("logs", "-l", f"job_id={job_id}",
                    "--all-containers=true", "--tail=500", "--prefix", capture=True))
            except subprocess.SubprocessError:
                pass
        if (self.state / "images.json").exists():
            shutil.copy2(self.state / "images.json", evidence / "images.json")
        for path in evidence.iterdir():
            if path.is_file():
                value = path.read_text(errors="replace")
                for secret in self.redactions:
                    if secret:
                        value = value.replace(secret, "[REDACTED]")
                path.write_text(value)

    def down(self):
        self.cluster_guard()
        self.namespace_guard()
        # Runtime-created objects carry their own labels. Keep PVCs and credentials.
        self.kube("delete", "jobs,configmaps,secrets,networkpolicies", "-l", JOB_SELECTOR,
                  "--ignore-not-found", "--wait=true", timeout=120)
        self.kube("delete", "deployments,services,configmaps,roles,rolebindings,serviceaccounts", "-l", SELECTOR,
                  "--ignore-not-found", "--wait=true", timeout=120)
        if self.args.command == "reset":
            self.kube("delete", "pvc,secrets", "-l", SELECTOR, "--ignore-not-found", timeout=120)
        print("Environment stopped; " + ("local data removed." if self.args.command == "reset" else "PVC/data preserved."))

    def execute(self):
        command = self.args.command
        if command == "render":
            print(json.dumps(render(self.args.namespace, self.images()), indent=2))
        elif command in ("doctor", "up", "secret", "smoke", "godog"):
            getattr(self, command)()
        elif command == "build-load":
            self.build_load()
        elif command in ("down", "reset"):
            if command == "reset":
                require(self.args.confirm_reset == self.args.namespace,
                        f"Reset deletes environment data. Pass --confirm-reset {self.args.namespace}.")
            self.down()
        else:
            self.cluster_guard()
            self.namespace_guard()
            if command == "forward":
                self.kube("port-forward", "--address=127.0.0.1", "svc/evalhub", "18080:8088", "18081:8081",
                          timeout=None)
            elif command == "logs":
                self.kube("get", "pods,jobs,pvc")
                self.kube("logs", "deployment/evalhub", "--all-containers=true", "--tail=200")
                self.kube("get", "events", "--sort-by=.metadata.creationTimestamp")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["doctor", "build-load", "render", "up", "forward", "smoke",
                                            "secret", "logs", "down", "reset", "godog"])
    for flag, env, default in [
        ("context", "KIND_CONTEXT", "kind-kind"), ("cluster", "KIND_CLUSTER", "kind"),
        ("namespace", "KIND_NAMESPACE", "evalhub-local"),
        ("connection", "CONTAINER_CONNECTION", ""), ("tag", "IMAGE_TAG", ""),
        ("contrib-revision", "CONTRIB_REVISION", CONTRIB_REVISION),
    ]:
        parser.add_argument("--" + flag, default=os.getenv(env, default))
    workspace = ROOT.parents[2]
    parser.add_argument("--evalhub-repo", type=Path, default=Path(os.getenv("EVALHUB_REPO", workspace / "eval-hub")))
    parser.add_argument("--contrib-repo", type=Path, default=Path(os.getenv("CONTRIB_REPO", workspace / "eval-hub-contrib")))
    parser.add_argument("--mode", choices=["mock", "external"], default=os.getenv("MODE", "mock"))
    parser.add_argument("--key-file", type=Path, default=os.getenv("LLM_API_KEY_FILE"))
    parser.add_argument("--confirm-reset", default="")
    parser.add_argument("--godog-timeout", type=int, default=os.getenv("GODOG_TIMEOUT_SECONDS", "7200"),
                        help="overall Godog runtime limit in seconds (default: 7200)")
    args = parser.parse_args()
    for field in ("context", "cluster", "namespace"):
        value = getattr(args, field)
        require(len(value) <= 63 and re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", value),
                f"Invalid {field}; use a DNS label up to 63 characters.")
    require(args.namespace not in ("default", "kube-system", "kube-public", "kube-node-lease"),
            "Use a dedicated environment namespace.")
    require(not args.tag or re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", args.tag), "Invalid image tag.")
    require(args.mode in ("mock", "external"), "MODE must be mock or external.")
    require(args.godog_timeout > 0, "Godog timeout must be positive.")
    Environment(args).execute()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
