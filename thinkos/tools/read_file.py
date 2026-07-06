"""ReadFileAdapter — read text files with optional offset/limit."""

import os


class ReadFileAdapter:
    name = "read_file"
    description = "Read a text file. Returns content with line numbers."

    def execute(self, params: dict, context: dict) -> dict:
        path = params.get("path", "")
        if not path:
            return {"status": "error", "call_id": params.get("call_id", ""),
                    "output": "", "error": "Missing required parameter: 'path'",
                    "artifacts": [], "receipts": []}

        # Path traversal check
        if ".." in path.split(os.sep):
            return {"status": "error", "call_id": params.get("call_id", ""),
                    "output": "", "error": "Path traversal rejected",
                    "artifacts": [], "receipts": []}

        if not os.path.isfile(path):
            return {"status": "error", "call_id": params.get("call_id", ""),
                    "output": "", "error": f"File not found: {path}",
                    "artifacts": [], "receipts": []}

        offset = params.get("offset")
        limit = params.get("limit")

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except PermissionError:
            return {"status": "error", "call_id": params.get("call_id", ""),
                    "output": "", "error": f"Permission denied: {path}",
                    "artifacts": [], "receipts": []}
        except Exception as e:
            return {"status": "error", "call_id": params.get("call_id", ""),
                    "output": "", "error": str(e),
                    "artifacts": [], "receipts": []}

        total_lines = len(lines)
        start = (offset - 1) if offset and offset >= 1 else 0
        end = start + limit if limit else total_lines

        selected = lines[start:end]
        output = "".join(f"{i+1}|{l}" for i, l in enumerate(selected, start=start + 1))

        return {
            "status": "ok",
            "call_id": params.get("call_id", ""),
            "output": output,
            "error": None,
            "artifacts": [{"path": path, "lines_read": len(selected), "total_lines": total_lines}],
            "receipts": [],
        }
