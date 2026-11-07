"""The shared FastMCP app instance. Tool modules import `mcp` from here to
register themselves via `@mcp.tool()`; server.py imports the tool modules
(for their registration side effects) and runs the app.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    name="watsonx-platform",
    instructions=(
        "Product-agnostic access to IBM watsonx's Common Core Services (CCS): "
        "Projects, Assets, Connections, and Jobs — shared by every watsonx product "
        "(watsonx.ai, watsonx.data, DataStage, Pipelines, ...). "
        "Call `authenticate` first (or set WX_* env vars) before any other tool. "
        "Tools are grouped by confidence: auth/projects/assets(read+basic "
        "create)/connections/jobs(create+read) are production-confident, live-"
        "verified against a real CPD cluster and a SaaS dev tenant. "
        "upload_asset_file, update_job (schedule edits), job-run lifecycle tools, "
        "update_connection/delete_connection, and all project-member tools are "
        "EXPERIMENTAL — implemented from the spec's contract but not (or not fully) "
        "live-verified; treat their exact request/response shapes as provisional."
    ),
)
