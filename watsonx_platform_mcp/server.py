"""Entrypoint: registers every tool module (import side effects) and runs the
stdio MCP server. See README.md for the WX_* env vars and the
production-confident-vs-experimental tool breakdown.
"""

from __future__ import annotations

from .app import mcp

# Imported for registration side effects (@mcp.tool()) — order doesn't matter.
from .tools import (  # noqa: F401  (re-exported for convenience / testing)
    auth_tool,
    assets,
    connections,
    job_runs,
    jobs,
    members,
    projects,
)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
