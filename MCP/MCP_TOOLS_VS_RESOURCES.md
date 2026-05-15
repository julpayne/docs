# MCP: Tools vs Resources

In **MCP (Model Context Protocol)**, tools and resources are two different ways a server exposes capabilities to a client (e.g. Cursor). The split is roughly **actions vs. data**.

## Tools — things you *do*

A **tool** is a **callable function** with a defined name, input schema, and output.

- **Purpose:** Perform an operation (search, create a ticket, run a query, call an API).
- **Interaction:** The client **invokes** it with arguments.
- **Shape:** Like LLM “function calling” — parameters in, structured result out.
- **Semantics:** Often has side effects (write, delete, post). Clients may ask for approval before running sensitive tools.

**Examples:** `search_issues`, `create_pull_request`, `run_sql`.

## Resources — things you *read*

A **resource** is **addressable, read-oriented content** identified by a **URI**.

- **Purpose:** Supply **context** (docs, file contents, config, logs, database rows as static snapshots).
- **Interaction:** The client **lists** and **fetches** it. You don’t “run” a resource; you load it into context.
- **Shape:** URI + metadata (name, description, MIME type) + body (text, JSON, binary, etc.).
- **Semantics:** Intended to be **read-only** from the protocol’s perspective. Updates, if any, are usually via subscriptions or the server refreshing what’s behind the URI—not via a generic “execute resource” call.

**Examples:** `file:///project/README.md`, `config://settings`, `postgres://schema/users`.

## Quick comparison

| | **Tool** | **Resource** |
|---|----------|--------------|
| **Metaphor** | Verb (action) | Noun (document/data) |
| **Client operation** | Call with arguments | List / fetch by URI |
| **Typical use** | Mutate or compute | Ground the model in facts |
| **Side effects** | Common | Not expected on fetch |
| **Discovery** | Tool descriptors (name, schema) | Resource catalog (URI, mime, description) |

## How to choose when building a server

- Expose a **tool** when the client needs to **run something** with inputs and get a result (especially if behavior varies by parameters or can change state).
- Expose a **resource** when you have **stable or browsable content** the model should **read** for context (files, schemas, dashboards as data).

Many servers offer **both**: resources for “what exists / what it looks like,” tools for “do something with it.”

## Cursor integration

In Cursor, MCP is wired as:

- **Tools** → invoke via the MCP tool API (check the tool schema/descriptor before calling).
- **Resources** → discover via list, then fetch by server name and resource URI.

Tool and resource descriptors for enabled MCP servers are exposed under the editor’s MCP configuration (per-server `tools/` and `resources/` descriptor files when the server advertises them).
