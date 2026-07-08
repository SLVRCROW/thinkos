"""StdinConnector — JSON-Lines over stdio with bounded line reading."""

import json
import sys

_DRAIN_CHUNK = 65536  # 64 KB chunks for draining oversized lines


class StdinConnector:
    """Concrete connector that reads JSON-Lines from stdin and writes to stdout.

    Line reading is bounded by *max_line_bytes* to prevent oversized input
    from consuming unbounded memory.  Lines that exceed the limit are
    rejected (drained from the stream) and an error is written to stderr.
    """

    def __init__(self, max_line_bytes: int = 1048576):
        self._max_line_bytes = max_line_bytes

    # ------------------------------------------------------------------
    # reading
    # ------------------------------------------------------------------

    def read_message(self) -> dict | None:
        raw = sys.stdin.buffer.readline(self._max_line_bytes + 1)
        if not raw:
            return None  # EOF

        # If we got exactly max+1 bytes and the last byte is not a newline,
        # the line was truncated — it exceeds the limit.
        if len(raw) == self._max_line_bytes + 1 and not raw.endswith(b'\n'):
            self._drain_oversized()
            self.write_error(
                f"Line exceeds maximum size of {self._max_line_bytes} bytes"
            )
            return None

        try:
            line = raw.decode('utf-8').strip()
        except UnicodeDecodeError as e:
            self.write_error(f"Invalid UTF-8 in input: {e}")
            return None

        if not line:
            return None

        try:
            return json.loads(line)
        except json.JSONDecodeError as e:
            self.write_error(f"Malformed JSON: {e}")
            return None

    def _drain_oversized(self):
        """Drain the remainder of an oversized line in bounded chunks.

        After readline(max+1) returned a truncated line, the rest of the
        line is still in the buffer.  Read in fixed-size chunks until we
        hit a newline or EOF, so the next read_message() starts on a
        fresh line.
        """
        while True:
            chunk = sys.stdin.buffer.readline(_DRAIN_CHUNK)
            if not chunk or chunk.endswith(b'\n'):
                break

    # ------------------------------------------------------------------
    # writing
    # ------------------------------------------------------------------

    def write_response(self, response: dict):
        line = json.dumps(response, separators=(",", ":"))
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

    def write_error(self, msg: str):
        sys.stderr.write(msg + "\n")
        sys.stderr.flush()

    def close(self):
        pass
