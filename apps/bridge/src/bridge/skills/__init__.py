"""Skill executors for the C3PO bridge.

Each skill is a self-contained function that returns a result dict matching
the MCP tool's output shape. Skills assume `init_dds` has already been called
and the StateSampler is running (both done by `bridge.mcp_server` at startup).
"""
