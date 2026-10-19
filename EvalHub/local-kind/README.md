# EvalHub on local kind

Scripts for the build and deployment portion of the [local testing plan](local-kind-testing-plan.md).
They reuse the existing ARM64 `kind` cluster and Podman engine. Source checkouts stay unchanged;
all generated files are under ignored `bin/`. Python 3.10+, Podman, kind, kubectl, git and make are required.
The Godog stage also requires host Go (with the toolchain required by `go.mod`) and `oc`, which the
upstream suite invokes for some cluster scenarios. Container build dependencies are installed inside
the image builds. The initial build needs internet access
and can take several minutes, especially for the upstream Inspect adapter dependencies and NLTK fixtures.

## Build and deploy

From this repository's root:

```bash
make -C EvalHub/local-kind local-env-all
```

This runs script checks, cluster preflight, image build/load, deployment, the end-to-end smoke test,
and then every upstream Godog scenario selected by `@cluster`
in order, stopping at the first failure, including when Make runs with `-j`. Mock mode is the default.
The environment and test evidence remain available afterward. External mode requires the credential Secret and endpoint
settings described below before running `local-env-all MODE=external`.

To run individual steps:

```bash
make -C EvalHub/local-kind local-env-doctor
make -C EvalHub/local-kind local-env-build-load
make -C EvalHub/local-kind local-env-up
make -C EvalHub/local-kind local-env-forward
```

Keep the last command running. It binds the API to `127.0.0.1:18080` and metrics to `127.0.0.1:18081`.
From another terminal:

```bash
curl --fail http://127.0.0.1:18080/api/v1/health
make -C EvalHub/local-kind local-env-test
make -C EvalHub/local-kind local-env-logs
```

`local-env-test` runs the smoke test and then the upstream cluster suite. Use `local-env-smoke` for only
the five-sample smoke test, or `local-env-godog` for only the upstream suite against the existing deployment.

The smoke command manages its own temporary loopback port-forwards. It runs the real Inspect adapter
and real EvalHub sidecar against five baked-in samples, then checks API completion, Kubernetes Job
completion, five reported samples, benchmark metrics, benchmark/aggregate scores of 1.0 and exactly five
fixture requests to the mock. Run one evaluation at a time: the mock journal is shared and reset per run.
The mock uses non-streaming chat completions, and the request explicitly disables the Responses API.
Inspect caching is disabled for the smoke task so repeated runs still make five model requests.
The pinned scorer reports `exact/mean`, which is used for the primary score.

The build command supplies `TARGETARCH=arm64`, builds three uniquely tagged images (combined EvalHub,
real upstream Inspect adapter plus task, and Python support services), saves OCI archives, and loads them
through `kind load image-archive`. Images use `IfNotPresent`; no registry or Docker daemon is needed.
The same support image runs the gateway and mock as separate containers. The adapter build uses the
upstream requirements and constraints at contrib revision `24e92727de1948355dcd4c5ba0752b97e6e3121f`.
The EvalHub checkout's current revision and dirty state are recorded, including changes newer than the plan.
The build also imports the five-sample task in a network-disabled adapter container before loading it.
Build records and image IDs are saved to `bin/<context>-<namespace>/images.json` only after all loads succeed.

The deployment includes namespace-scoped RBAC, explicit service/provider configuration, a gateway,
the API Service, mock Service and a 1 GiB SQLite PVC using StorageClass `standard`. API rollout uses
`Recreate` to avoid multiple SQLite writers. Configuration changes alter the pod template checksum.
The API runs without `--local` and without `EVALHUB_INSTANCE_NAME`; callbacks use the in-cluster gateway.
The runtime keeps its existing projected-token isolation for generated evaluation pods.

## Configuration

Defaults are `kind-kind`, cluster `kind`, namespace `evalhub-local`, and sibling
`eval-hub` / `eval-hub-contrib` source checkouts. The API endpoint and port are discovered automatically
using `kubectl cluster-info --context kind-kind` (or the configured context).
The preflight checks a loopback HTTPS API endpoint, Ready ARM64 nodes, `standard` storage, and that the selected
Podman connection owns the same kind nodes and exposed API port. It does not create or resize the cluster.
An existing namespace or resource must have this environment's ownership label before it can be modified.

Environment variables configure Make targets:

```bash
export EVALHUB_REPO=/path/to/eval-hub
export CONTRIB_REPO=/path/to/eval-hub-contrib
export CONTAINER_CONNECTION=podman-machine-default
make -C EvalHub/local-kind local-env-doctor
```

Choose the Podman connection that owns the existing node. `podman system connection list` shows available
connections. The scripts use `KIND_EXPERIMENTAL_PROVIDER=podman` and propagate `CONTAINER_CONNECTION`.

To inspect the discovered endpoint yourself:

```bash
kubectl cluster-info --context kind-kind
```

Recreating the cluster with a different API port requires no script configuration change.

Other variables: `KIND_CONTEXT`, `KIND_CLUSTER`, `KIND_NAMESPACE`, `IMAGE_TAG`, `CONTRIB_REVISION`, `MODE`.
Context must equal `kind-<cluster>`. Source overrides and explicit tags can also be passed as CLI flags:

```bash
bash EvalHub/local-kind/local-env.sh doctor --connection podman-machine-default
bash EvalHub/local-kind/local-env.sh render --tag preview > /tmp/evalhub-kind.json
```

`render` is offline and writes Kubernetes JSON to stdout. Without `--tag`, it uses the last successful build.
An explicit build tag must be unused; deployments verify that every image is already loaded on every node.
Use a new tag after changing source or local assets. The contrib revision check rejects uncommitted adapter
changes. Overriding its revision means validating compatibility with that source yourself.

## External model

External requests are selected explicitly with `MODE=external`. The deployment stays the same, and the
mock remains available; each evaluation request chooses its model endpoint. Store a key in a file outside
the repository or in the ignored `EvalHub/local-kind/secrets/` directory. The file must contain only the key.

```bash
export LLM_API_KEY_FILE=/path/outside/repository/llm-api-key
export LLM_BASE_URL=https://your-endpoint.example/v1
export LLM_MODEL=your-model
make -C EvalHub/local-kind local-env-secret
make -C EvalHub/local-kind local-env-up MODE=external
make -C EvalHub/local-kind local-env-test MODE=external
```

Run mock setup first to create the environment namespace. The secret command creates `external-llm`
with the `api-key` key using stdin and server-side apply; the key is never a shell argument or request JSON
field. The sidecar resolves it using EvalHub's existing credential-reference mechanism. Use an endpoint
reachable from the cluster, with system-trusted TLS, and include the API base path once (usually `/v1`).
Host loopback addresses point inside the evaluation pod. No TLS verification bypass is configured.

The task sets 64 output tokens, one connection, a 30-second model timeout and two client retries. The runner
polls for at most ten minutes, followed by a bounded Kubernetes completion check. Endpoint support governs
generation settings. External mode checks valid results and five samples; quality scores are observational.
It makes real model calls and can incur charges. A polling timeout retains resources for diagnosis; it does
not guarantee that a broken framework or pod has stopped. Use `down` to stop outstanding environment Jobs.

## Diagnostics and cleanup

Smoke evidence is saved under `bin/<context>-<namespace>/runs/<run-id>/`: request, latest response, status
history, Job/Pod/ConfigMap/Event JSON, API and container logs, image IDs and source revisions. No Secret
objects are exported, and the selected external API key is redacted from saved evidence. External logs
and model responses can contain provider data; review before sharing.
Completed and failed evaluation resources are retained for inspection.

## Upstream Godog cluster suite

The final test stage compiles `tests/features` from `EVALHUB_REPO`, then runs its Godog test binary against
the deployed kind service. It uses exactly `GODOG_TAGS=@cluster` with no tag exclusions, all feature files,
one scenario at a time, and no stop on the first scenario failure. It does not start an embedded EvalHub
server. It uses the checkout that supplied the API image; it does not change branches or fetch `main`.

```bash
make -C EvalHub/local-kind local-env-godog
```

The wrapper manages temporary API and metrics port-forwards and sets `SERVER_URL`, `METRICS_URL`,
`X_TENANT`, `X_USER`, `KUBERNETES_NAMESPACE`, and a temporary, private `KUBECONFIG` containing only the
selected context. `AUTH_TOKEN` is a dummy development token for this gateway profile. Model settings use
the same mock/external mode as the smoke test. Mock mode uses the deployed `local-mock-llm` dummy Secret;
external mode uses `external-llm`. No model credential is passed as an environment variable.

The run saves JUnit XML, Cucumber JSON, `tests.log`, source revision, and `godog-summary.json` under a
`runs/<timestamp>-godog-<id>/` evidence directory. Source and fixture paths are linked into a temporary
working directory so the upstream logger writes into the evidence directory. The source checkout stays
unchanged. Compilation has a 15-minute limit; the suite has a two-hour overall limit, configurable with
`GODOG_TIMEOUT_SECONDS`. Per-job waits default to five minutes; set `WAIT_DEADLINE` to override them.
The Godog process exit status propagates to Make. Missing/empty reports fail the command. Upstream skips
are counted explicitly as skips, and a successful process with skips is reported as `passed_with_skips`.

The `@cluster` selection includes other benchmark providers, GPU/Kueue, hardware profiles, MLflow, OCI,
and PVC/Git/HF data scenarios. Their environment variables are inherited by the runner. The minimal
Inspect deployment and five-fixture mock do not supply all these prerequisites or answer arbitrary
benchmark prompts. These scenarios may fail or be skipped by upstream code; including them does not
claim the full cluster suite passes. Extend the environment with the real required providers and addons
to make those scenarios pass. Reports preserve the actual outcomes without adding exclusions or stubs.

## Teardown

```bash
make -C EvalHub/local-kind local-env-down
# Explicitly delete this environment's PVC/data and managed credentials as well:
CONFIRM_RESET=evalhub-local make -C EvalHub/local-kind local-env-reset
```

Both commands discover and validate the endpoint and verify namespace ownership first. Normal teardown removes managed
deployments, Services, configuration, RBAC and runtime evaluation resources, and preserves the PVC and
credential Secret. Reset additionally deletes environment-labeled PVCs and Secrets. The namespace, shared
cluster and saved evidence remain. Do not keep unrelated workloads in this dedicated namespace.

For `ImagePullBackOff`, confirm the loaded tag and Podman connection. For a pending PVC, check `standard`
and local-path provisioning. For stuck evaluations, inspect API and job container logs with the printed run
ID; adapter acceptance alone does not prove Kubernetes completion.

## Validation and boundaries

```bash
make -C EvalHub/local-kind check
```

Checks cover manifest wiring, scope/ownership/endpoint/reset guards, all mock fixtures, rejected model
requests, and gateway identity replacement. The HTTP tests bind temporary localhost ports. Rendering and
unit checks do not prove that image builds or live evaluations succeed; run the commands above for that.
The single-user gateway overwrites identity headers and is a development trust fixture. Operator/security
parity, enforcing NetworkPolicies, infrastructure-failure reconciliation, failure injection, hard-delete
regression coverage, init staging, MLflow and OCI addons remain work described in the plan.
