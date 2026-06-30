"""Minimal web UI (stdlib only — no extra dependencies).

A thin viewer over the same engine the CLI uses: upload candidate sources (or point
at a server-side folder), edit the output config, run, and view / download the
produced profile JSON. The engine does the real work; this stays dependency-free
(no Flask/React) — file uploads are parsed with a small stdlib multipart reader
because cgi.FieldStorage was removed in Python 3.13+.

Run:  python web/app.py   then open http://127.0.0.1:8000
"""

from __future__ import annotations

import html
import json
import re
import shutil
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from string import Template
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from eightfold.cli import _INPUT_EXTS, _expand_inputs  # noqa: E402
from eightfold.models import OutputConfig  # noqa: E402
from eightfold.pipeline import run  # noqa: E402

DEFAULT_INPUTS = str(ROOT / "samples" / "inputs")
DEFAULT_CONFIG = json.dumps({"fields": None, "include_provenance": True,
                             "include_confidence": True, "on_missing": "null"}, indent=2)

# string.Template ($name) is used instead of str.format so the CSS/JS braces below
# can stay literal and readable. No literal '$' appears in the CSS/JS.
PAGE = Template("""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Eightfold · Profile Transformer</title>
<style>
 :root{
   --bg:#0b1220; --panel:#111c30; --panel2:#0d1626; --line:#243449; --ink:#e6edf6;
   --mut:#8aa0bb; --accent:#3b82f6; --accent2:#2563eb; --ok:#34d399; --err:#f87171;
 }
 *{box-sizing:border-box}
 body{font:14px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;
   background:radial-gradient(1200px 600px at 80% -10%,#16233c 0,var(--bg) 55%);
   color:var(--ink);min-height:100vh}
 header{padding:18px 28px;display:flex;align-items:center;gap:14px;
   border-bottom:1px solid var(--line);background:rgba(17,28,48,.6);backdrop-filter:blur(6px)}
 .logo{width:30px;height:30px;border-radius:8px;flex:0 0 auto;
   background:linear-gradient(135deg,#60a5fa,#2563eb);box-shadow:0 4px 14px rgba(37,99,235,.5)}
 h1{font-size:17px;margin:0;letter-spacing:.2px}
 .sub{color:var(--mut);font-size:12px;margin-top:1px}
 main{display:grid;grid-template-columns:minmax(360px,1fr) minmax(420px,1.2fr);
   gap:20px;padding:22px 28px;align-items:start}
 @media(max-width:880px){main{grid-template-columns:1fr}}
 .card{background:linear-gradient(180deg,var(--panel),var(--panel2));border:1px solid var(--line);
   border-radius:14px;padding:18px;box-shadow:0 10px 30px rgba(0,0,0,.25)}
 label{display:block;font-size:11px;text-transform:uppercase;letter-spacing:.6px;
   color:var(--mut);margin:14px 0 6px;font-weight:600}
 label:first-of-type{margin-top:0}
 input[type=text],textarea{width:100%;background:#0a1322;color:var(--ink);
   border:1px solid var(--line);border-radius:9px;padding:10px 12px;
   font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px}
 input[type=text]:focus,textarea:focus{outline:0;border-color:var(--accent);
   box-shadow:0 0 0 3px rgba(59,130,246,.18)}
 textarea{min-height:190px;resize:vertical;line-height:1.5}
 .drop{border:1.5px dashed #335;border-radius:11px;padding:18px;text-align:center;
   cursor:pointer;transition:.15s;background:#0a1322}
 .drop:hover,.drop.drag{border-color:var(--accent);background:#0d1830}
 .drop b{color:var(--ink)} .drop .hint{color:var(--mut);font-size:12px;margin-top:4px}
 .drop .ic{font-size:22px;opacity:.8}
 #flist{margin-top:8px;font-size:12px;color:var(--mut);word-break:break-word;min-height:16px}
 .row{display:flex;align-items:center;gap:8px;margin-top:14px}
 .chk{display:flex;align-items:center;gap:8px;color:var(--mut);font-size:13px;cursor:pointer}
 .chk input{width:16px;height:16px;accent-color:var(--accent)}
 .btn{background:linear-gradient(180deg,var(--accent),var(--accent2));color:#fff;border:0;
   padding:11px 20px;border-radius:9px;cursor:pointer;font-size:13.5px;font-weight:600;
   box-shadow:0 6px 16px rgba(37,99,235,.35)}
 .btn:hover{filter:brightness(1.07)} .btn:active{transform:translateY(1px)}
 .btn.ghost{background:#13233c;border:1px solid var(--line);box-shadow:none;color:var(--ink)}
 .btn:disabled{opacity:.45;cursor:not-allowed;box-shadow:none}
 .bar{display:flex;align-items:center;gap:8px;margin-bottom:10px;flex-wrap:wrap}
 .bar .spacer{flex:1}
 .chip{font-size:11.5px;padding:3px 9px;border-radius:999px;border:1px solid var(--line);
   color:var(--mut);background:#0c1626}
 .chip.ok{color:#062;background:rgba(52,211,153,.16);border-color:rgba(52,211,153,.4);color:var(--ok)}
 .chip.bad{color:var(--err);background:rgba(248,113,113,.14);border-color:rgba(248,113,113,.4)}
 pre{background:#060c18;border:1px solid var(--line);border-radius:11px;padding:14px;
   overflow:auto;max-height:72vh;white-space:pre-wrap;font-size:12.5px;
   font-family:ui-monospace,SFMono-Regular,Menlo,monospace;margin:0}
 pre.err{color:var(--err)}
 .muted{color:var(--mut);font-size:12px}
</style></head><body>
<header>
 <div class="logo"></div>
 <div><h1>Eightfold · Canonical Profile Transformer</h1>
 <div class="sub">messy multi-source inputs → one clean, provenance-tagged profile</div></div>
</header>
<main>
 <form class="card" method="post" action="/run" enctype="multipart/form-data">
  <label>Candidate sources</label>
  <div id="drop" class="drop" onclick="document.getElementById('files').click()">
   <div class="ic">⬆︎</div>
   <div><b>Drop files here</b> or <b style="color:#60a5fa">browse</b></div>
   <div class="hint">.csv · .json · .txt — multiple allowed (drops add to the list)</div>
   <input id="files" type="file" name="files" multiple
          accept=".csv,.json,.txt" style="display:none">
  </div>
  <div id="flist">No files selected — will use the server path below</div>
  <div style="margin-top:4px"><a href="#" id="clear" class="muted" style="display:none"
       onclick="clearfiles();return false">✕ clear selection</a></div>

  <label>Or server-side path (used when no files are uploaded)</label>
  <input type="text" name="inputs" value="$inputs">

  <label>Output config (JSON) — fields:null emits the full default schema</label>
  <textarea name="config" spellcheck="false">$config</textarea>

  <div class="row">
   <label class="chk"><input type="checkbox" name="llm"> enable LLM enrichment</label>
   <span class="spacer"></span>
   <button class="btn" type="submit">Run pipeline ▸</button>
  </div>
 </form>

 <div class="card">
  <div class="bar">
   <strong style="font-size:13px">Result</strong>
   $chips
   <span class="spacer"></span>
   <button class="btn ghost" id="cp" type="button" onclick="cp()" $btndis>Copy</button>
   <button class="btn" type="button" onclick="dl()" $btndis>Download JSON</button>
  </div>
  <pre class="$cls">$result</pre>
 </div>
</main>
<script>
 var RESULT = $resultjs;
 // A DataTransfer is the authoritative file list so drops/browses ACCUMULATE
 // (the sample sources live in different folders, so they're added in batches).
 var dt = new DataTransfer();
 var inp = document.getElementById('files');
 function has(f){ for (var i=0;i<dt.files.length;i++){
   if (dt.files[i].name===f.name && dt.files[i].size===f.size) return true; } return false; }
 function refresh(){
   inp.files = dt.files;
   var n = dt.files.length, el = document.getElementById('flist');
   el.textContent = n ? (n + ' file(s): ' + Array.from(dt.files).map(function(f){return f.name}).join(', '))
                      : 'No files selected — will use the server path below';
   document.getElementById('clear').style.display = n ? 'inline' : 'none';
 }
 function add(list){ for (var i=0;i<list.length;i++){ if(!has(list[i])) dt.items.add(list[i]); } refresh(); }
 function clearfiles(){ dt = new DataTransfer(); refresh(); }
 inp.addEventListener('change', function(){ add(inp.files); });
 var dz = document.getElementById('drop');
 ['dragenter','dragover'].forEach(function(e){dz.addEventListener(e,function(ev){
   ev.preventDefault(); dz.classList.add('drag');});});
 ['dragleave','drop'].forEach(function(e){dz.addEventListener(e,function(ev){
   ev.preventDefault(); dz.classList.remove('drag');});});
 dz.addEventListener('drop', function(ev){ add(ev.dataTransfer.files); });
 function dl(){ if(!RESULT) return;
   var b = new Blob([RESULT], {type:'application/json'});
   var a = document.createElement('a'); a.href = URL.createObjectURL(b);
   a.download = 'profiles.json'; document.body.appendChild(a); a.click();
   a.remove(); URL.revokeObjectURL(a.href);
 }
 function cp(){ if(!RESULT) return;
   navigator.clipboard.writeText(RESULT);
   var t = document.getElementById('cp'), o = t.textContent;
   t.textContent = 'Copied!'; setTimeout(function(){t.textContent = o;}, 1200);
 }
</script>
</body></html>""")


def _chips(result: dict | None) -> str:
    if not result:
        return ""
    n = len(result.get("candidates", []))
    errs = len(result.get("errors", []))
    failed = sum(1 for r in result.get("report", []) if r.get("status") == "failed")
    out = [f'<span class="chip ok">{n} candidate(s)</span>']
    out.append(f'<span class="chip {"bad" if errs else ""}">{errs} error(s)</span>')
    if failed:
        out.append(f'<span class="chip bad">{failed} source(s) failed</span>')
    return "".join(out)


def render(inputs=DEFAULT_INPUTS, config=DEFAULT_CONFIG, result_text="(run to see output)",
           cls="", chips="", result_dict=None):
    # Escape everything user/data-controlled before it lands in HTML: a candidate value
    # containing <, &, " or </pre> must render verbatim, never break the page or get
    # silently swallowed (that would contradict the engine's honesty contract).
    has_json = result_dict is not None
    # JS string literal for the client-side download/copy (raw JSON, not the escaped view).
    result_js = json.dumps(result_text).replace("</", "<\\/") if has_json else '""'
    return PAGE.substitute(
        inputs=html.escape(inputs, quote=True),
        config=html.escape(config),
        result=html.escape(result_text),
        cls=cls,
        chips=chips,
        resultjs=result_js,
        btndis="" if has_json else "disabled",
    )


def _parse_multipart(body: bytes, boundary: bytes) -> tuple[dict[str, str], list[tuple[str, bytes]]]:
    """Tiny multipart/form-data reader (cgi was removed in 3.13+). Returns plain text
    fields and uploaded (filename, bytes) parts."""
    fields: dict[str, str] = {}
    files: list[tuple[str, bytes]] = []
    for part in body.split(b"--" + boundary):
        if not part or part in (b"--\r\n", b"--", b"\r\n"):
            continue
        if b"\r\n\r\n" not in part:
            continue
        raw_head, data = part.split(b"\r\n\r\n", 1)
        if data.endswith(b"\r\n"):
            data = data[:-2]
        head = raw_head.decode("utf-8", "replace")
        disp = next((ln for ln in head.split("\r\n") if ln.lower().startswith("content-disposition")), "")
        name_m = re.search(r'name="([^"]*)"', disp)
        file_m = re.search(r'filename="([^"]*)"', disp)
        if not name_m:
            continue
        if file_m is not None:
            if file_m.group(1).strip():
                files.append((file_m.group(1), data))
        else:
            fields[name_m.group(1)] = data.decode("utf-8", "replace")
    return fields, files


def _inputs_from_uploads(files: list[tuple[str, bytes]]) -> tuple[str | None, list[str]]:
    """Save supported uploads to a temp dir; return (tmpdir, expanded file list). The
    engine detects source type by extension + content, so a flat dir is fine (a GitHub
    fixture is still recognized by its login/html_url keys)."""
    tmpdir = tempfile.mkdtemp(prefix="eightfold_ui_")
    for fname, data in files:
        safe = Path(fname).name  # strip any path components (no traversal)
        if safe and Path(safe).suffix.lower() in _INPUT_EXTS:
            (Path(tmpdir) / safe).write_bytes(data)
    return tmpdir, _expand_inputs([tmpdir])


class Handler(BaseHTTPRequestHandler):
    def _send(self, page: str):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(page.encode("utf-8"))

    def do_GET(self):  # noqa: N802
        self._send(render())

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        ctype = self.headers.get("Content-Type", "")
        if ctype.startswith("multipart/form-data") and "boundary=" in ctype:
            boundary = ctype.split("boundary=", 1)[1].strip().strip('"').encode()
            fields, uploads = _parse_multipart(body, boundary)
        else:  # urlencoded fallback (e.g. curl --data)
            form = parse_qs(body.decode("utf-8"))
            fields = {k: v[0] for k, v in form.items()}
            uploads = []

        inputs_raw = (fields.get("inputs") or DEFAULT_INPUTS).strip()
        config_raw = fields.get("config") or DEFAULT_CONFIG
        use_llm = "llm" in fields
        tmpdir: str | None = None
        try:
            cfg_dict = json.loads(config_raw)
            cfg_dict.pop("_comment", None)
            config = OutputConfig.model_validate(cfg_dict)

            if uploads:
                tmpdir, files = _inputs_from_uploads(uploads)
                src_label = f"{len(files)} uploaded file(s)"
            else:
                files = _expand_inputs([inputs_raw])
                src_label = inputs_raw

            if not files:
                self._send(render(inputs_raw, config_raw,
                                  f"no usable input files (.csv/.json/.txt) found in: {src_label}",
                                  cls="err"))
                return

            result = run(files, config, use_llm=use_llm)
            self._send(render(inputs_raw, config_raw,
                              json.dumps(result, indent=2, ensure_ascii=False),
                              chips=_chips(result), result_dict=result))
        except Exception as exc:  # noqa: BLE001
            self._send(render(inputs_raw, config_raw, f"{type(exc).__name__}: {exc}", cls="err"))
        finally:
            if tmpdir:
                shutil.rmtree(tmpdir, ignore_errors=True)

    def log_message(self, *args):  # silence default logging
        pass


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f"Eightfold UI on http://127.0.0.1:{port}  (Ctrl-C to stop)")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
