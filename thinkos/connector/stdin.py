"""StdinConnector — JSON-Lines over stdio."""

import sys
import json


class StdinConnector:
    """Concrete connector that reads JSON-Lines from stdin and writes to stdout."""

    def read_message(self) -> dict | None:
        line = sys.stdin.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            return None
        try:
            return json.loads(line)
        except json.JSONDecodeError as e:
            self.write_error(f"Malformed JSON: {e}")
            return None

    def write_response(self, response: dict):
        line = json.dumps(response, separators=(",", ":"))
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

    def write_error(self, msg: str):
        sys.stderr.write(msg + "\n")
        sys.stderr.flush()

    def close(self):
        pass
