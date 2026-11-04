"""Audacity 3.x mod-script-pipe client.

Audacity 4 does not ship mod-script-pipe (checked 4.0.2: no pipe code in the
binary), so this talks to Audacity 3.2+ only.
"""

from __future__ import annotations

import json
import os
import sys
import threading

TROUBLESHOOT = """
Could not connect to Audacity's scripting pipe.

  1. Open Audacity 3 (Audacity 4 has no scripting pipe yet).
  2. Edit > Preferences > Modules: set 'mod-script-pipe' to 'Enabled'.
  3. Restart Audacity, then run this again (Audacity must stay open).
"""


class PipeError(RuntimeError):
    pass


class AudacityPipe:
    def __init__(self, timeout: float = 5.0):
        if sys.platform == "win32":
            to_name, from_name, self.eol = r"\\.\pipe\ToSrvPipe", r"\\.\pipe\FromSrvPipe", "\r\n\0"
        else:
            uid = os.getuid()
            to_name = f"/tmp/audacity_script_pipe.to.{uid}"
            from_name = f"/tmp/audacity_script_pipe.from.{uid}"
            self.eol = "\n"
            if not (os.path.exists(to_name) and os.path.exists(from_name)):
                raise PipeError(TROUBLESHOOT)
        # Opening a FIFO blocks forever if nobody is on the other end -> do it on a thread.
        box: dict = {}

        def _open():
            try:
                box["to"] = open(to_name, "w", encoding="utf-8")
                box["from"] = open(from_name, "r", encoding="utf-8")
            except OSError as e:
                box["err"] = e

        th = threading.Thread(target=_open, daemon=True)
        th.start()
        th.join(timeout)
        if th.is_alive() or "err" in box:
            raise PipeError(TROUBLESHOOT)
        self._to, self._from = box["to"], box["from"]

    def send(self, command: str, check: bool = True) -> str:
        self._to.write(command + self.eol)
        self._to.flush()
        lines = []
        while True:
            line = self._from.readline()
            if line == "":
                raise PipeError("Audacity closed the pipe (did it crash or quit?)")
            if line == "\n" and lines:
                break
            lines.append(line)
        reply = "".join(lines)
        if check and "BatchCommand finished: OK" not in reply:
            raise PipeError(f"Audacity rejected:\n  {command}\n  -> {reply.strip()}")
        return reply

    def get_json(self, command: str):
        reply = self.send(command)
        body = reply[: reply.rfind("BatchCommand finished")]
        start = min(i for i in (body.find("["), body.find("{")) if i >= 0)
        return json.loads(body[start:])

    def tracks(self) -> list[dict]:
        return self.get_json("GetInfo: Type=Tracks Format=JSON")

    def close(self):
        for f in (getattr(self, "_to", None), getattr(self, "_from", None)):
            try:
                f and f.close()
            except OSError:
                pass


def q(s: str) -> str:
    """Quote a value for a pipe command. Forward slashes work on Windows too."""
    return '"' + str(s).replace("\\", "/").replace('"', "'") + '"'
