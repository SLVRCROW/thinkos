"""WriteFileAdapter — write text content to files."""

import os


class WriteFileAdapter:
    name = "write_file"
    description = "Write content to a file. Overwrites existing content."

    def execute(self, params: dict, context: dict) -> dict:
        path = params.get("path", "")
        content = params.get("content", "")

        if not path:
            return {"status": "error", "call_id": params.get("call_id", ""),
                    "output": "", "error": "Missing required parameter: 'path'",
                    "artifacts": [], "receipts": []}
        if not content:
            return {"status": "error", "call_id": params.get("call_id", ""),
                    "output": "", "error": "Missing required parameter: 'content'",
                    "artifacts": [], "receipts": []}

        # Path traversal check
        if ".." in path.split(os.sep):
            return {"status": "error", "call_id": params.get("call_id", ""),
                    "output": "", "error": "Path traversal rejected",
                    "artifacts": [], "receipts": []}

        try:
            parent = os.path.dirname(path)
            if parent and not os.path.exists(parent):
                os.makedirs(parent, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
        except PermissionError:
            return {"status": "error", "call_id": params.get("call_id", ""),
                    "output": "", "error": f"Permission denied: {path}",
                    "artifacts": [], "receipts": []}
        except Exception as e:
            return {"status": "error", "call_id": params.get("call_id", ""),
                    "output": "", "error": str(e),
                    "artifacts": [], "receipts": []}

        return {
            "status": "ok",
            "call_id": params.get("call_id", ""),
            "output": f"Wrote {len(content)} bytes to {path}",
            "error": None,
            "artifacts": [{"path": path, "bytes_written": len(content)}],
            "receipts": [],
        }
