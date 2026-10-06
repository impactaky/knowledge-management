"""Synthetic newline JSON-RPC server; no external store or services."""
import json
import sys
import time

mode = sys.argv[1] if len(sys.argv) > 1 else "structured"
if mode == "blocked_input":
    time.sleep(60)

initialized = False
for line in sys.stdin:
    request = json.loads(line)
    method = request["method"]
    if method == "initialize":
        assert request["params"]["capabilities"] == {}
        result = {"protocolVersion": "2024-11-05", "capabilities": {}, "serverInfo": {"name": "fixture", "version": "1"}}
    elif method == "notifications/initialized":
        initialized = True
        continue
    else:
        assert method == "tools/call" and initialized
        if mode == "timeout":
            time.sleep(60)
        if mode == "exit":
            sys.exit(7)
        if mode == "malformed":
            print("not JSON", flush=True)
            continue
        if mode == "rpc_error":
            print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32603, "message": "synthetic failure"}}), flush=True)
            continue
        name = request["params"]["name"]
        value = {"tool": name, "arguments": request["params"]["arguments"]}
        if mode == "tool_error":
            result = {"isError": True, "content": [{"type": "text", "text": "synthetic failure"}]}
        elif name == "get_catalog":
            result = {"content": [{"type": "text", "text": "# Synthetic remote Catalog"}]}
        elif mode == "text":
            result = {"content": [{"type": "text", "text": json.dumps(value)}]}
        else:
            result = {"structuredContent": value, "content": [{"type": "text", "text": "ignored"}]}
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
