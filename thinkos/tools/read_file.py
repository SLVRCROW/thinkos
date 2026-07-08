"""ReadFileAdapter — read text files with optional offset/limit.

Path sandboxing is enforced by default. See thinkos/tools/sandbox.py.
File size is checked against max_read_output_bytes before reading.
"""

import os
from thinkos.tools.sandbox import resolve_path, SandboxError


class ReadFileAdapter:
    name = "read_file"
    description = "Read a text file. Returns content with line numbers."

    def execute(self, params: dict, context: dict) -> dict:
        path = params.get("path", "")
        call_id = params.get("call_id", "")
        if not path:
            return _error(call_id, "Missing required parameter: 'path'")

        allowed_root = context.get("allowed_root")
        try:
            safe_path = resolve_path(path, allowed_root)
        except SandboxError as e:
            return _error(call_id, str(e))

        if not os.path.isfile(safe_path):
            return _error(call_id, f"File not found: {path}")

        # Check file size before reading into memory
        limits = context.get("limits", {})
        max_bytes = limits.get("max_read_output_bytes", 1048576)
        file_size = os.path.getsize(safe_path)
        if max_bytes and file_size > max_bytes:
            return _error(
                call_id,
                f"File exceeds maximum readable size of {max_bytes} bytes "
                f"(file is {file_size} bytes)"
            )

        offset = params.get("offset")
        limit = params.get("limit")

        try:
            with open(safe_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except PermissionError:
            return _error(call_id, f"Permission denied: {path}")
        except Exception as e:
            return _error(call_id, str(e))

        total_lines = len(lines)
        start = (offset - 1) if offset and offset >= 1 else 0
        end = start + limit if limit else total_lines
        selected = lines[start:end]
        output = "".join(f"{i+1}|{l}" for i, l in enumerate(selected, start=start + 1))

        return {
            "status": "ok",
            "call_id": call_id,
            "output": output,
            "error": None,
            "artifacts": [{"path": safe_path, "lines_read": len(selected), "total_lines": total_lines}],
            "receipts": [],
        }


def _error(call_id: str, msg: str) -> dict:
    return {
        "status": "error",
        "call_id": call_id,
        "output": "",
        "error": msg,
        "artifacts": [],
        "receipts": [],
    }
