# EvalHub MCP: Gateway integration vs. rhoai-mcp plugins

This document records conclusions from comparing **evalhub-mcp** with the contributor blueprint in [opendatahub-io/rhoai-mcp PR #29](https://github.com/opendatahub-io/rhoai-mcp/pull/29) and with **Red Hat MCP Gateway** (Connectivity Link / Kuadrant).

## Summary

| Integration model | Can evalhub-mcp use it? | Recommendation |
|-------------------|------------------------|----------------|
| **rhoai-mcp domain/composite plugin** (PR #29) | No, not without a full reimplementation | Do not pursue unless eval-hub tools must live inside the RHOAI monolith |
| **MCP Gateway** (federated MCP servers on OpenShift) | Yes — natural fit | Prefer this for enterprise agent access with auth, federation, and governance |

**evalhub-mcp should be deployed and registered as its own MCP server behind MCP Gateway**, not embedded as a Python domain plugin inside rhoai-mcp.

---

## What rhoai-mcp PR #29 is

[PR #29](https://github.com/opendatahub-io/rhoai-mcp/pull/29) adds a **contributor blueprint for plugins inside the rhoai-mcp monolith**. It is not documentation for Red Hat MCP Gateway.

The PR introduces:

- A reference **domain plugin** (`src/rhoai_mcp/domains/_example/`) — models, Kubernetes client, MCP tools, registry entry
- **Templates** (`docs/contributing/templates/`) for new domain and composite plugins
- Guides: [adding a domain](https://github.com/opendatahub-io/rhoai-mcp/blob/main/docs/contributing/adding-a-domain.md), adding a composite
- Registration via rhoai-mcp’s **Pluggy** hook system (`rhoai_register_tools`, `get_core_plugins()`, etc.)

Domain plugins are **in-process Python modules** that extend a single FastMCP server. They call **Kubernetes / OpenShift AI APIs** (CRDs, ConfigMaps, etc.). They are not separate MCP server processes and not `MCPServerRegistration` resources.

---

## evalhub-mcp today

evalhub-mcp is a **standalone Go MCP server** (`cmd/evalhub_mcp`, `internal/evalhub_mcp/server`):

| Aspect | evalhub-mcp |
|--------|-------------|
| Runtime | Go, [Model Context Protocol Go SDK](https://github.com/modelcontextprotocol/go-sdk) |
| Backend | eval-hub **REST API** via `pkg/evalhubclient` |
| Transports | `stdio` (local), `http` (Streamable HTTP), `http-sse` (legacy) |
| Capabilities | Tools, resources, prompts |
| Deployment | Separate `evalhub-mcp` workload (see [MCP.md](../MCP.md)) |

Tools include `submit_evaluation`, `cancel_job`, and `get_job_status`, plus resources and prompts registered in `RegisterHandlers`.

---

## Can evalhub-mcp be a rhoai-mcp domain plugin?

**Not as-is.** The PR #29 pattern assumes:

- Python + FastMCP + Pydantic
- Pluggy hooks and `domains/registry.py`
- A K8s-oriented client layer per domain

evalhub-mcp is a different stack and backend. Fitting it into rhoai-mcp would mean **reimplementing** the same MCP surface in Python against the eval-hub API — duplicating the existing Go server, not migrating it.

A rhoai-mcp “eval-hub domain” only makes sense if product requirements explicitly require **one unified RHOAI MCP endpoint** with all tools in a single process, and the team accepts maintaining two implementations (Go + Python) or deprecating evalhub-mcp.

---

## Can evalhub-mcp work with MCP Gateway?

**Yes.** That is the appropriate enterprise integration.

[Red Hat MCP Gateway](https://www.redhat.com/en/blog/control-your-ai-agent-traffic-scale-model-context-protocol-gateway-red-hat-openshift-now-technology-preview) (technology preview in Connectivity Link, upstream Kuadrant) sits **between AI agents and MCP servers**. It provides:

- **Federation** — multiple MCP servers behind one gateway endpoint
- **Authentication and authorization** — same policy model as Connectivity Link (e.g. JWT via `AuthPolicy`)
- **Tool prefixes** — avoid name collisions when aggregating servers (e.g. `evalhub_submit_evaluation`)
- **Virtual servers** — smaller tool sets for delegation and token efficiency
- **Scaling** — session store for multi-replica gateway deployments

Backends are registered with Kubernetes resources such as `MCPGatewayExtension` and `MCPServerRegistration`, which point at an **HTTPRoute** in front of the MCP server — not at in-repo Python plugins.

evalhub-mcp already aligns with that model:

- HTTP/SSE exposure suitable for routing (operator deploys `evalhub-mcp` Service; see [MCP.md](../MCP.md))
- Bearer token auth compatible with cluster credentials (`oc whoami -t` in local testing)
- Focused tool surface suitable for federation and optional prefixing

### Reference architecture

```text
┌─────────────┐     ┌──────────────────────┐     ┌─────────────┐     ┌──────────────┐
│  AI agent   │────▶│  MCP Gateway         │────▶│ evalhub-mcp │────▶│  eval-hub    │
│             │     │  (Connectivity Link) │     │  (Go MCP)   │     │  REST API    │
└─────────────┘     └──────────┬───────────┘     └─────────────┘     └──────────────┘
                               │
                               ├────▶ rhoai-mcp (optional, separate registration)
                               └────▶ other MCP servers
```

Gateway configuration (tool prefix, auth, routes) is **platform/operator concern**, not a change to evalhub-mcp source code.

---

## Recommendations

### Preferred: MCP Gateway

1. Deploy **evalhub-mcp** as today (operator / `spec.mcp.enabled` in the EvalHub CR).
2. Expose it via **Gateway API** (`HTTPRoute` → `evalhub-mcp` Service).
3. Register with **`MCPServerRegistration`** (optional `toolPrefix`, e.g. `evalhub_`).
4. Attach **`AuthPolicy`** on the gateway MCP listener for JWT/OAuth as required by the platform.
5. Prefer **Streamable HTTP** (`--transport http`) for new clients; use `http-sse` only for legacy inspectors (see [MCP.md](../MCP.md)).

### Avoid unless required: rhoai-mcp plugin

Only add an eval-hub domain inside rhoai-mcp if:

- Agents must use **exactly one** MCP server URL for all RHOAI + eval-hub tools, and
- Federation at the gateway is insufficient, and
- The team will own a Python reimplementation or long-term dual maintenance.

### Optional internal refactor (unrelated to MCP Gateway)

evalhub-mcp could adopt a **plugin-style registry in Go** (register tools/resources/prompts from packages) similar in *spirit* to PR #29. That would improve modularity inside the Go binary only; it does not replace gateway registration.

---

## Related links

- [rhoai-mcp PR #29 — plugin blueprint](https://github.com/opendatahub-io/rhoai-mcp/pull/29)
- [opendatahub-io/rhoai-mcp](https://github.com/opendatahub-io/rhoai-mcp)
- [Red Hat: MCP Gateway on OpenShift (technology preview)](https://www.redhat.com/en/blog/control-your-ai-agent-traffic-scale-model-context-protocol-gateway-red-hat-openshift-now-technology-preview)
- [EvalHub MCP development notes](../MCP.md)
- [AGENTS.md — MCP server section](../AGENTS.md#mcp-server)

---

## Document history

| Date | Notes |
|------|--------|
| 2026-05-29 | Initial conclusions (evalhub-mcp vs. PR #29 blueprint vs. MCP Gateway) |
