"""Thin MCP entrypoint that wires tool handlers from modular components."""

from mcp.server.fastmcp import FastMCP

from tools import (
    approve_command,
    delete_file,
    execute_command,
    list_directory,
    read_file,
    restore_backup,
    server_info,
    write_file,
)

mcp = FastMCP("ai-runtime-guard")

for tool in [
    server_info,
    restore_backup,
    execute_command,
    approve_command,
    read_file,
    write_file,
    delete_file,
    list_directory,
]:
    mcp.tool()(tool)


if __name__ == "__main__":
    mcp.run()
