# Blender Manual and Runtime Knowledge

**English** | [中文](blender-knowledge_CN.md)

Blender MCP combines official documentation with the exact capabilities of the
connected Blender build. This keeps authored explanations, experimental node
definitions, and installed Essentials assets available without placing an
entire Manual in model context.

## Tools

| Tool | Use it for | Blender required |
| --- | --- | --- |
| `get_blender_documentation_context` | Resolve the exact build, source channels, language, and any fallback before retrieval. | Only with `version="auto"` |
| `search_blender_docs` | Search bounded official Manual, Python API, and Release Notes indexes. | Only with `version="auto"` |
| `get_blender_doc_page` | Read one sanitized page or exact heading section from a search result. | Only with `version="auto"` |
| `search_geometry_node_types` | Discover node types constructible in Geometry Nodes in the running build. | Yes |
| `get_geometry_node_type_schema` | Inspect compact sockets, node-owned properties, and dynamic items for an exact runtime type. | Yes |
| `search_blender_node_assets` | Inspect installed official Essentials node assets in a disposable scope. | Yes |

The first three tools are read-only network tools. Runtime and Essentials tools
are read-only Blender operations and clean up all temporary data blocks before
returning.

## Recommended query strategy

1. Search the Manual for concepts and workflows.
2. Search the Python API for RNA identifiers or scripting details.
3. Search Release Notes when the feature is new or changed.
4. Ask `search_geometry_node_types` for the exact node type accepted by the
   connected build.
5. Ask `get_geometry_node_type_schema` for the compact live socket and property
   contract. Use `detail="full"` only when inherited RNA is truly necessary.
6. Search installed Essentials for official high-level node groups and examples.

For example, a Blender 5.2 prerelease XPBD query can combine:

```text
search_blender_docs(
  query="XPBD Solver",
  version="auto",
  sources=["manual", "python_api", "release_notes"]
)
search_geometry_node_types(query="XPBD")
get_geometry_node_type_schema(
  node_type="GeometryNodeXPBDSolver",
  detail="compact"
)
search_blender_node_assets(
  library="geometry_nodes_dynamics_assets.blend",
  detail="summary"
)
```

The documentation result explains the feature; the runtime schema remains the
authority for sockets and properties in that exact Blender build.

## Versions and fallbacks

`version` accepts:

- `auto`: read the exact connected Blender build; fail clearly when Blender is
  disconnected;
- `major.minor` or `major.minor.patch`: resolve that explicit documentation
  version without connecting to Blender;
- `current`: use the current stable documentation channel;
- `dev`: use the development documentation channel.

Manual, Python API, and Release Notes are resolved independently. A prerelease
build can therefore use the development Manual/API while keeping numeric
Release Notes. Every channel or language substitution appears in structured
`fallback` metadata; it is never silent.

The Manual supports normalized language codes such as `en`, `zh-hans`, and
`zh-hant`. Python API and Release Notes are English-only. A missing localized
Manual index or page may retry the same channel in English and records the
requested and resolved languages.

Published documentation can lag behind a release candidate. A missing page is
not treated as evidence that a live node is absent; use runtime discovery for
the exact answer.

## Bounded retrieval and source attribution

Search returns at most 20 ranked records. Page retrieval accepts only a
source-relative identifier returned by search and returns at most 50,000
characters. Scripts, styles, navigation, forms, and other page chrome are
removed. Results retain canonical official URLs for verification.

Network access is restricted to HTTPS pages on:

- `docs.blender.org` for the Manual and Python API;
- `developer.blender.org` for Release Notes.

Arbitrary URLs, credentials, custom ports, path traversal, unsafe redirects,
unexpected content types, oversized responses, and unbounded redirect chains
are rejected.

## Cache and offline behavior

Fetched indexes and pages use a per-user cache, not the repository or `.blend`
file. The default locations are:

- Windows: `%LOCALAPPDATA%\BlenderMCP\Cache\docs-v1`;
- macOS: `~/Library/Caches/blender-mcp/docs-v1`;
- Linux: `$XDG_CACHE_HOME/blender-mcp/docs-v1` or
  `~/.cache/blender-mcp/docs-v1`.

Set `BLENDER_MCP_CACHE_DIR` to override the parent cache directory. Entries have
a 24-hour freshness window, honor ETag and Last-Modified validators, and are
bounded to 128 MiB. Atomic writes, hashes, and schema/versioned keys prevent
partial or incompatible entries from being used.

During a timeout, connection failure, or HTTP 5xx response, an expired entry may
be returned as `stale_fallback`. Responses expose cache status, age, fetch time,
and the triggering error. Client errors such as HTTP 404 never use stale data.
Permission failures disable persistence for that response and are reported as
`cache_unavailable`; live retrieval can still succeed.

## Privacy and project safety

Documentation requests send the query only through official indexed pages and
page URLs; they do not upload the Blender scene. Runtime schema discovery creates
temporary node groups, and Essentials discovery temporarily appends bundled
assets, but both operations verify cleanup before returning. Neither tool saves
the project.

`execute_blender_code` and Geometry Nodes patch tools have separate mutation
semantics. Their safety and rollback rules are documented in
[Geometry Nodes automation](geometry-nodes.md).

## Compatibility

The public tools are available to any client that loads this MCP server; a
client-side Skill is not required. Live acceptance covers Blender 4.2.22 LTS,
Blender 5.1.2, and Blender 5.2 LTS RC.
