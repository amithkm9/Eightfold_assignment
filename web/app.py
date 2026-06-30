"""Minimal web UI (stdlib only — no extra dependencies).

Intentionally low-polish, per the brief: it just lets you point at an inputs folder,
paste/edit an output config, and view the produced profile JSON. The engine does the
real work; this is a thin viewer.

Run:  python web/app.py   then open http://127.0.0.1:8000
"""

from __future__ import annotations

import html
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from eightfold.cli import _expand_inputs  # noqa: E402
from eightfold.models import OutputConfig  # noqa: E402
from eightfold.pipeline import run  # noqa: E402

DEFAULT_INPUTS = str(ROOT / "samples" / "inputs")
DEFAULT_CONFIG = json.dumps({"fields": None, "include_provenance": True,
                             "include_confidence": True, "on_missing": "null"}, indent=2)

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Eightfold Profile Transformer</title>
<style>
 body{{font:14px/1.5 system-ui,sans-serif;margin:0;background:#0f172a;color:#e2e8f0}}
 header{{padding:16px 24px;background:#1e293b;border-bottom:1px solid #334155}}
 h1{{font-size:18px;margin:0}} .sub{{color:#94a3b8;font-size:12px}}
 main{{display:flex;gap:16px;padding:16px 24px;flex-wrap:wrap}}
 .col{{flex:1;min-width:380px}}
 label{{display:block;font-size:12px;color:#94a3b8;margin:8px 0 4px}}
 input,textarea{{width:100%;box-sizing:border-box;background:#1e293b;color:#e2e8f0;
   border:1px solid #334155;border-radius:6px;padding:8px;font-family:ui-monospace,monospace}}
 textarea{{min-height:220px}} button{{margin-top:12px;background:#3b82f6;color:#fff;border:0;
   padding:10px 18px;border-radius:6px;cursor:pointer;font-size:14px}}
 pre{{background:#020617;border:1px solid #334155;border-radius:6px;padding:12px;overflow:auto;
   max-height:75vh;white-space:pre-wrap}}
 .err{{color:#f87171}}
</style></head><body>
<header><h1>Eightfold &middot; Canonical Profile Transformer</h1>
<div class="sub">messy multi-source inputs &rarr; one clean, provenance-tagged profile</div></header>
<main>
 <form class="col" method="post" action="/run">
  <label>Inputs (file or folder)</label>
  <input name="inputs" value="{inputs}">
  <label>Output config (JSON) &mdash; fields:null uses the default schema</label>
  <textarea name="config">{config}</textarea>
  <label><input type="checkbox" name="llm" style="width:auto"> enable LLM enrichment</label>
  <button type="submit">Run pipeline</button>
 </form>
 <div class="col"><label>Result</label><pre class="{cls}">{result}</pre></div>
</main></body></html>"""


def render(inputs=DEFAULT_INPUTS, config=DEFAULT_CONFIG, result="(run to see output)", cls=""):
    # Escape everything user/data-controlled before it lands in HTML: a candidate
    # value containing <, &, " or </pre> must render verbatim, never break the page
    # or get silently swallowed (that would contradict the engine's honesty contract).
    return PAGE.format(inputs=html.escape(inputs, quote=True),
                       config=html.escape(config),
                       result=html.escape(result), cls=cls)


class Handler(BaseHTTPRequestHandler):
    def _send(self, html: str):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def do_GET(self):  # noqa: N802
        self._send(render())

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        form = parse_qs(self.rfile.read(length).decode("utf-8"))
        inputs_raw = form.get("inputs", [DEFAULT_INPUTS])[0]
        config_raw = form.get("config", [DEFAULT_CONFIG])[0]
        use_llm = "llm" in form
        try:
            cfg_dict = json.loads(config_raw)
            cfg_dict.pop("_comment", None)
            config = OutputConfig.model_validate(cfg_dict)
            files = _expand_inputs([inputs_raw])
            if not files:
                # A bad path otherwise yields an empty-but-"successful" result, which
                # reads as "found nobody" rather than "wrong path". Say so plainly.
                self._send(render(inputs_raw, config_raw,
                                  f"no usable input files (.csv/.json/.txt) found at: {inputs_raw}",
                                  cls="err"))
                return
            result = run(files, config, use_llm=use_llm)
            self._send(render(inputs_raw, config_raw,
                              json.dumps(result, indent=2, ensure_ascii=False)))
        except Exception as exc:  # noqa: BLE001
            self._send(render(inputs_raw, config_raw, f"{type(exc).__name__}: {exc}", cls="err"))

    def log_message(self, *args):  # silence default logging
        pass


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f"Eightfold UI on http://127.0.0.1:{port}  (Ctrl-C to stop)")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
