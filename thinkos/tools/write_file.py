"""WriteFileAdapter — write text content to files.

Path sandboxing is enforced by default. See thinkos/tools/sandbox.py.
Content size is limited by max_write_content_bytes from config.
"""

import os
from thinkos.tools.sandbox import resolve_path, SandboxError
from thinkos.tools.validate import validate_params

SCHEMA = {
    "path": {"required": True, "type": str},
    "content": {"required": True, "type": str},
    "call_id": {"required": False, "type": str},
}


class WriteFileAdapter:
    name = "write_file"
    description = "Write content to a file. Overwrites existing content."

    def execute(self, params: dict, context: dict) -> dict:
        # Parameter validation
        errors = validate_params(params, SCHEMA)
        if errors:
            call_id = params.get("call_id", "") if isinstance(params, dict) else ""
            return _error(call_id, "; ".join(errors))

        path = params.get("path", "")
        call_id = params.get("call_id", "")

        if not path:
            return _error(call_id, "Missing required parameter: 'path'")

        # content is allowed to be empty string; only reject if key is absent
        if "content" not in params:
            return _error(call_id, "Missing required parameter: 'content'")
        content = params.get("content", "")

        # Content size limit
        limits = context.get("limits", {})
        max_bytes = limits.get("max_write_content_bytes", 10485760)
        if max_bytes and len(content.encode("utf-8")) > max_bytes:
            return _error(
                call_id,
                f"Content exceeds maximum size of {max_bytes} bytes"
            )

        allowed_root = context.get("allowed_root")
        try:
            safe_path = resolve_path(path, allowed_root)
        except SandboxError as e:
            return _error(call_id, str(e))

        try:
            parent = os.path.dirname(safe_path)
            if parent and not os.path.exists(parent):
                os.makedirs(parent, exist_ok=True)
            with open(safe_path, "w", encoding="utf-8") as f:
                f.write(content)
        except PermissionError:
            return _error(call_id, f"Permission denied: {path}")
        except Exception as e:
            return _error(call_id, str(e))

        return {
            "status": "ok",
            "call_id": call_id,
            "output": f"Wrote {len(content)} bytes to {path}",
            "error": None,
            "artifacts": [{"path": safe_path, "bytes_written": len(content)}],
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
