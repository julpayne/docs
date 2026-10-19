"""Render Kubernetes JSON (also valid YAML), without templating tools or PyYAML."""
import hashlib
import json

OWNER = {"app.kubernetes.io/managed-by": "evalhub-local-kind"}
SELECTOR = "app.kubernetes.io/managed-by=evalhub-local-kind"

# Derived from internal/eval_hub/runtimes/k8s/k8s_helper.go. No CRD permissions.
RULES = [
    {"apiGroups": ["batch"], "resources": ["jobs"],
     "verbs": ["get", "list", "create", "patch", "delete"]},
    {"apiGroups": [""], "resources": ["configmaps", "secrets"],
     "verbs": ["get", "list", "create", "update", "delete"]},
    {"apiGroups": [""], "resources": ["pods"], "verbs": ["get", "list"]},
    {"apiGroups": [""], "resources": ["pods/log"], "verbs": ["get"]},
    {"apiGroups": [""], "resources": ["events"], "verbs": ["create", "patch"]},
    {"apiGroups": ["networking.k8s.io"], "resources": ["networkpolicies"],
     "verbs": ["get", "create", "update", "delete"]},
]


def render(namespace, images):
    def obj(kind, name, api="v1", **fields):
        metadata = {"name": name, "labels": dict(OWNER)}
        if kind != "Namespace":
            metadata["namespace"] = namespace
        return {"apiVersion": api, "kind": kind, "metadata": metadata, **fields}

    config = {
        "service": {"host": "127.0.0.1", "port": 8080,
                    "eval_init_image": images["evalhub"], "termination_file": "/tmp/termination-log"},
        "database": {"driver": "sqlite", "url": "file:/data/evalhub.db?cache=shared"},
        "prometheus": {"enabled": True, "host": "0.0.0.0", "port": 8081},
        "otel": {"enabled": False},
        "sidecar": {
            "local_mode": False, "base_url": "http://localhost:8080",
            "eval_hub": {"base_url": f"http://evalhub.{namespace}.svc.cluster.local:8088",
                         "ca_cert_path": "", "insecure_skip_verify": False},
            "sidecar_container": {"image": images["evalhub"]},
        },
    }
    provider = {
        "id": "inspect", "name": "Local Inspect", "title": "Local Inspect",
        "description": "Five offline fixtures with the real Inspect adapter and sidecar",
        "runtime": {"k8s": {
            "image": images["inspect"], "image_pull_policy": "if_not_present",
            "entrypoint": ["python", "main.py"],
            "cpu_request": "250m", "memory_request": "512Mi",
            "cpu_limit": "2", "memory_limit": "4Gi",
            "env": [{"name": "INSPECT_EVAL_MAX_SAMPLES", "value": "1"},
                    {"name": "INSPECT_EVAL_MAX_CONNECTIONS", "value": "1"},
                    {"name": "HF_HUB_OFFLINE", "value": "1"},
                    {"name": "HF_DATASETS_OFFLINE", "value": "1"}],
        }},
        "benchmarks": [{"id": "inspect/custom", "name": "Local smoke task", "category": "custom",
                        "metrics": ["exact/mean"],
                        "primary_score": {"metric": "exact/mean", "lower_is_better": False},
                        "pass_criteria": {"threshold": 1.0}}],
    }
    data = {"config.yaml": json.dumps(config, indent=2), "inspect.yaml": json.dumps(provider, indent=2)}
    checksum = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    security = {"runAsNonRoot": True, "runAsUser": 1000, "runAsGroup": 1000,
                "fsGroup": 1000, "seccompProfile": {"type": "RuntimeDefault"}}
    container_security = {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}}

    def container(name, image, command, port):
        return {"name": name, "image": image, "imagePullPolicy": "IfNotPresent",
                "command": command, "ports": [{"containerPort": port}],
                "securityContext": container_security,
                "resources": {"requests": {"cpu": "50m", "memory": "64Mi"},
                              "limits": {"cpu": "500m", "memory": "256Mi"}}}

    api = container("api", images["evalhub"], ["/app/eval-hub", "--configdir=/app/config"], 8080)
    api["resources"] = {"requests": {"cpu": "100m", "memory": "256Mi"},
                        "limits": {"cpu": "1", "memory": "1Gi"}}
    api["volumeMounts"] = [{"name": "config", "mountPath": "/app/config", "readOnly": True},
                           {"name": "data", "mountPath": "/data"}]
    gateway = container("gateway", images["support"], ["python3", "/app/support.py", "gateway"], 8088)
    gateway["env"] = [{"name": "TENANT", "value": namespace}]
    gateway["readinessProbe"] = {"httpGet": {"path": "/api/v1/health", "port": 8088},
                                 "periodSeconds": 3, "timeoutSeconds": 3}
    mock = container("mock", images["support"], ["python3", "/app/support.py", "mock"], 8000)
    mock["readinessProbe"] = {"httpGet": {"path": "/health", "port": 8000}, "periodSeconds": 3}

    def deployment(name, containers, **extra):
        labels = {**OWNER, "app": name}
        return obj("Deployment", name, "apps/v1", spec={
            "replicas": 1, "strategy": {"type": "Recreate"}, "selector": {"matchLabels": {"app": name}},
            "template": {"metadata": {"labels": labels, "annotations": {"config-sha256": checksum}},
                         "spec": {"securityContext": security, "containers": containers,
                                  "automountServiceAccountToken": False, **extra}},
        })

    def service(name, ports):
        return obj("Service", name, spec={"selector": {"app": name}, "ports": ports})

    return {"apiVersion": "v1", "kind": "List", "items": [
        obj("Namespace", namespace),
        obj("ServiceAccount", "evalhub"),
        obj("Role", "evalhub", "rbac.authorization.k8s.io/v1", rules=RULES),
        obj("RoleBinding", "evalhub", "rbac.authorization.k8s.io/v1",
            roleRef={"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": "evalhub"},
            subjects=[{"kind": "ServiceAccount", "name": "evalhub", "namespace": namespace}]),
        obj("PersistentVolumeClaim", "evalhub-data", spec={"accessModes": ["ReadWriteOnce"],
            "storageClassName": "standard", "resources": {"requests": {"storage": "1Gi"}}}),
        obj("ConfigMap", "evalhub-local-config", data=data),
        obj("Secret", "local-mock-llm", type="Opaque", stringData={"api-key": "local-mock-only"}),
        deployment("evalhub", [api, gateway], serviceAccountName="evalhub", automountServiceAccountToken=True,
                   volumes=[{"name": "data", "persistentVolumeClaim": {"claimName": "evalhub-data"}},
                            {"name": "config", "configMap": {"name": "evalhub-local-config", "items": [
                                {"key": "config.yaml", "path": "config.yaml"},
                                {"key": "inspect.yaml", "path": "providers/inspect.yaml"}]}}]),
        service("evalhub", [{"name": "api", "port": 8088, "targetPort": 8088},
                             {"name": "metrics", "port": 8081, "targetPort": 8081}]),
        deployment("mock-llm", [mock]),
        service("mock-llm", [{"name": "http", "port": 8000, "targetPort": 8000}]),
    ]}
