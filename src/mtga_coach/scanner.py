"""Incremental reader for a log file that Arena keeps writing while the app runs.

The file grows during play and is truncated when the client restarts, so a reader that
loads the whole file each time either misses the session or re-reads it entirely. This
scanner accepts bytes as they arrive, returns the protocol records that are already
complete, and keeps a partial trailing record until its remaining bytes show up.
"""

import codecs
import json
import re

# A protocol record starts at the first '{' of a line: Arena writes either a one-line
# payload or a pretty-printed block, and both begin the JSON at the line's first brace.
CANDIDATE = re.compile(r"(?m)^[^\n{]*?\{")


class RecordScanner:
    def __init__(self):
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._buffer = ""
        self._base_line = 1
        self._cursor = 0
        self._cursor_line = 1
        self._stripped_bom = False
        self.warnings = []

    def _line_of(self, index):
        if index < self._cursor:
            return self._base_line + self._buffer.count("\n", 0, index)
        self._cursor_line += self._buffer.count("\n", self._cursor, index)
        self._cursor = index
        return self._cursor_line

    def _discard(self, consumed):
        if consumed <= 0:
            return
        newlines = self._buffer.count("\n", 0, consumed)
        self._base_line += newlines
        self._cursor = max(0, self._cursor - consumed)
        self._cursor_line = max(self._base_line, self._cursor_line - newlines)
        self._buffer = self._buffer[consumed:]

    def feed(self, data, final=False):
        """Records completed by these bytes. `final=True` declares the file exhausted."""
        text = self._decoder.decode(data, final)
        if not self._stripped_bom:
            text = text.lstrip("﻿")
            self._stripped_bom = True
        self._buffer += text
        return self._scan(final)

    def _scan(self, final):
        decoder = json.JSONDecoder()
        records = []
        position = 0
        consumed = 0
        while True:
            match = CANDIDATE.search(self._buffer, position)
            if match is None:
                # Nothing else starts a record: keep only a possibly partial last line.
                consumed = len(self._buffer) if final else max(consumed, self._buffer.rfind("\n") + 1)
                break
            start = match.end() - 1
            try:
                payload, end = decoder.raw_decode(self._buffer, start)
            except json.JSONDecodeError as error:
                truncated = error.pos >= len(self._buffer) - 1
                if truncated and not final:
                    consumed = max(consumed, match.start())
                    break  # More bytes are expected; the record is not invalid yet.
                if truncated:
                    self.warnings.append(
                        f"Incomplete JSON record at line {self._line_of(start)}; "
                        "the trailing stretch awaits more bytes or a fresh import.")
                    consumed = len(self._buffer)
                    break
                # Unity's device diagnostics print JSON-like dictionaries with bare enum
                # values. They are not protocol records and must not stop the scan.
                position = start + 1
                continue
            records.append({"line": self._line_of(start), "payload": payload})
            position = end
            consumed = end
        self._discard(consumed)
        return records

    def pending_bytes(self):
        return len(self._buffer)
