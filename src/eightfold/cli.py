"""Thin CLI surface.

    eightfold run --inputs <files|dirs> [--config cfg.json] [--out out.json] [--llm]

Points the engine at input files + an optional config, prints/writes the JSON. The
per-source run report goes to stderr so stdout stays a clean JSON document.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .models import OutputConfig
from .pipeline import run

_INPUT_EXTS = {".csv", ".json", ".txt"}


def _expand_inputs(paths: list[str]) -> list[str]:
    files: list[str] = []
    seen: set[str] = set()

    def _add(f: Path) -> None:
        # De-dup by resolved path so listing a dir AND a file inside it (or the same file
        # twice) doesn't feed duplicate records into the pipeline.
        key = str(f.resolve())
        if key not in seen:
            seen.add(key)
            files.append(str(f))

    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and f.suffix.lower() in _INPUT_EXTS:
                    _add(f)
        elif p.exists():
            _add(p)
        else:
            print(f"warning: input not found: {p}", file=sys.stderr)
    return files


def _load_config(path: str | None) -> OutputConfig:
    if not path:
        return OutputConfig()
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    raw.pop("_comment", None)
    return OutputConfig.model_validate(raw)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eightfold",
                                     description="Messy multi-source candidate data -> one canonical profile.")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="run the pipeline")
    run_p.add_argument("--inputs", "-i", nargs="+", required=True, help="input files or directories")
    run_p.add_argument("--config", "-c", default=None, help="output config JSON (default schema if omitted)")
    run_p.add_argument("--out", "-o", default=None, help="write JSON here (default: stdout)")
    run_p.add_argument("--llm", action="store_true", help="enable optional LLM enrichment of free text")
    run_p.add_argument("--compact", action="store_true", help="compact JSON (default: pretty)")
    run_p.add_argument("--jsonl", action="store_true", help="emit one candidate per line (stream-friendly)")
    run_p.add_argument("--strict", action="store_true",
                       help="exit non-zero if any source failed or any candidate errored")

    args = parser.parse_args(argv)

    if args.command == "run":
        inputs = _expand_inputs(args.inputs)
        if not inputs:
            print("error: no usable input files", file=sys.stderr)
            return 2
        try:
            config = _load_config(args.config)
        except (OSError, ValueError) as exc:
            print(f"error: could not load config {args.config!r}: {exc}", file=sys.stderr)
            return 2
        result = run(inputs, config, use_llm=args.llm)

        # Run report -> stderr.
        print("--- run report ---", file=sys.stderr)
        for r in result["report"]:
            print(f"  {r['source'] or '?':<16} {r['status']:<8} "
                  f"records={r['records']} claims={r['claims']}"
                  + (f"  ERROR: {r['error'].splitlines()[0]}" if r.get('error') else ""),
                  file=sys.stderr)
        print(f"  -> {len(result['candidates'])} candidate(s), {len(result['errors'])} error(s)",
              file=sys.stderr)
        for e in result["errors"]:
            print(f"     ! {e['candidate_id']}: {e['error']}", file=sys.stderr)

        if args.jsonl:
            payload = "\n".join(json.dumps(c, ensure_ascii=False) for c in result["candidates"])
        else:
            indent = None if args.compact else 2
            payload = json.dumps(result, indent=indent, ensure_ascii=False)
        if args.out:
            Path(args.out).write_text(payload + "\n", encoding="utf-8")
            print(f"wrote {args.out}", file=sys.stderr)
        else:
            print(payload)

        failed = result["errors"] or any(r["status"] == "failed" for r in result["report"])
        return 3 if (args.strict and failed) else 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
