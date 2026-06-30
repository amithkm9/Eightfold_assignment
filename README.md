# Eightfold — Canonical Candidate Profile Transformer

Turns messy, conflicting, multi-source candidate data into **one clean, canonical,
provenance-tagged profile** — with a runtime config that can reshape the output without
touching the engine.

> **Governing principle:** *wrong-but-confident is worse than honestly-empty.* When a value
> can't be trusted, the pipeline abstains to `null` — it never invents.

📄 **One-page design / "how I think": [`docs/design.md`](docs/design.md).**

---

## The idea in one picture

```
SourceFiles ─(detect)→ extract → [Claim] ─(normalize)→ ─(resolve / entity-match)→
            ─(fuse / survivorship)→  CanonicalProfile  ─(project + validate)→  Output
                                     (one rich record)   (config-driven, pure)
```

This is **CQRS for candidate data**: a rich canonical record is built **once** (write side),
then a **pure, deterministic projection layer** shapes it per the runtime config (read side).
The config is *data fed to a generic projector* — never logic in the merge engine.

Every value enters as a **`Claim`** carrying `source`, `method`, `raw_span`, and
`extracted_confidence`, so **provenance and confidence are invariants**, not afterthoughts.

---

## Sources covered

| Group | Source | Notes |
|---|---|---|
| **Structured** | Recruiter **CSV** | direct cell reads (highest trust) |
| **Structured** | **ATS JSON** | foreign field names → remapped to canonical |
| **Unstructured** | **GitHub** | pluggable client; fixtures by default (offline/deterministic) |
| **Unstructured** | Recruiter **notes (.txt)** | deterministic regex; optional Claude enrichment |

(≥1 structured + ≥1 unstructured, as required.)

---

## Quickstart

```bash
# 1. Install (Python 3.10+)
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2. Run on the sample inputs — DEFAULT schema
eightfold run --inputs samples/inputs --out out_default.json

# 3. Run with a CUSTOM runtime config (subset + remap + normalize)
eightfold run --inputs samples/inputs --config config/example_custom.json --out out_custom.json
```

The per-source run report prints to **stderr**; the JSON document goes to **stdout** (or `--out`).

### Minimal web UI (optional)

```bash
python web/app.py            # then open http://127.0.0.1:8000
```

Point it at an inputs folder, paste/edit a config, and view the produced profile JSON.
Intentionally low-polish — the engine is the point.

### Optional: Claude enrichment of free text

Off by default. Enable with `--llm` **and** an `ANTHROPIC_API_KEY` set:

```bash
export ANTHROPIC_API_KEY=sk-...
eightfold run --inputs samples/inputs --llm --out out_llm.json
```

The LLM (`claude-opus-4-8`, structured JSON output, **cached by input-hash** for
reproducibility) only *proposes* low-confidence skill/headline claims that can **never
overwrite** a structured value. Without a key it's a no-op — the run is unchanged.

---

## The runtime config (the "required twist")

```json
{
  "fields": [
    { "path": "full_name", "type": "string", "required": true },
    { "path": "primary_email", "from": "emails[0]", "type": "string", "required": true },
    { "path": "phone", "from": "phones[0]", "type": "string", "normalize": "E164" },
    { "path": "skills", "from": "skills[].name", "type": "string[]", "normalize": "canonical" }
  ],
  "include_confidence": true,
  "on_missing": "null"
}
```

- **Select** a subset of fields.
- **Remap** via a `from` path expression: dotted (`location.country`), indexed (`emails[0]`),
  array-map (`skills[].name`).
- **Normalize** per field (`E164`, `canonical`, `ISO3166`, `YYYY-MM`) — same registry used to
  build the canonical record.
- **Toggle** `include_provenance` / `include_confidence`.
- **`on_missing`**: `null` | `omit` | `error`.

`fields: null` (see `config/default_schema.json`) emits the full canonical schema. Output is
**validated against the requested schema** before it's returned.

---

## How conflicts are resolved (survivorship)

Per-field-**class**, not one global rule:

- **Multi-valued** (`emails`, `phones`, `skills`, `links`) → **union + dedup** (never drop a real value).
- **Identity/contact** (`full_name`, `location`, `headline`) → **winner by source-trust priority**
  (`recruiter_csv > ats_json > linkedin > github > resume > recruiter_notes`), tie-broken by
  extractor confidence then completeness.
- **Authority override** → GitHub is canonical for its own URL and languages.
- **Derived** (`years_experience`) → prefer a stated value, else compute from **closed**
  experience spans (open/"present" ranges are skipped so the result never depends on today's date).

**Confidence** is explainable: `clamp(source_trust × method_factor × normalize_factor +
corroboration_bonus − conflict_penalty, 0, 1)` — every number traces to inputs.

**Entity resolution** is deterministic: union-find over strong keys (email, E.164 phone,
github/linkedin URL) + a composite name+company key, then a **guarded fuzzy pass** (same
last-name/portfolio-host block, compatible name, ≥1 corroborating signal) so a GitHub display
name like *"Jane Q. Doe"* with no email still merges with the *"Jane Doe"* cluster.

---

## What the sample run demonstrates

The four sample sources describe **Jane Doe** with deliberate conflicts. The pipeline:

- picks **"Jane Doe"** (CSV) over **"Jane Q. Doe"** (GitHub) and records both in provenance;
- collapses **three phone formats** (`+1 (415) 555-0100`, `(415) 555-0100`, `415.555.0100`) into one E.164 number;
- **unions** two emails and canonicalizes skills across sources (`golang`→`Go`, `k8s`→`Kubernetes`), with corroborating `sources[]`;
- merges a malformed JSON source and an empty notes file **without crashing** (reported as `failed`/`empty`);
- isolates a garbage CSV row that can't satisfy a `required` field into `errors[]` instead of failing the batch.

---

## Tests & quality

```bash
pytest -q          # 34 tests
ruff check src tests   # lint (clean)
```

34 tests across normalization, the projection DSL, fusion/conflict resolution, end-to-end +
**gold-profile comparison** (`samples/expected/`), determinism, edge cases, and a dedicated
regression suite (`test_review_fixes.py`) locking in fixes from an architecture review
(conflicting-id merges, empty-company over-merge, fabricated months, overlapping-tenure
double-count, unique candidate ids, confidence-toggle validation). Ships `py.typed`; ruff + mypy
configured in `pyproject.toml`.

Useful flags: `--jsonl` (one candidate per line), `--strict` (non-zero exit if any source failed
or candidate errored), `--llm` (optional Claude enrichment), `--compact`.

---

## Constraints, honored

- **Deterministic & explainable** — same inputs → byte-identical output; every field traces to
  a `(source, method)`. (LLM path stays deterministic via input-hash caching.)
- **Robust** — a missing/garbage source degrades to a status, never a crash; unknown → `null`.
- **Scale** — **blocking is implemented** (last-name/portfolio-host buckets, with a bucket-size
  guard), so entity resolution is near-linear: **~10k records in <0.4s (≈27k rec/s)** on a laptop,
  not O(n²). `--jsonl` streams one candidate per line for large corpora.

## Deliberately descoped (honest scope)

- Cross-machine/**sharded** resolution & ML-learned matching (single-node blocking + a guarded
  fuzzy pass are implemented).
- Streaming I/O for multi-million-row corpora (JSONL output is offered; full streaming is future work).
- Exhaustive skill ontology (small, swappable synonym map).
- Resume PDF/DOCX layout parsing (notes/.txt covers the unstructured-prose path).
- Auth / multi-tenant concerns.

Rationale: maximize the graded core — **canonical ↔ projection separation, provenance/confidence,
honest-null** — over breadth.

---

## Layout

```
config/        default + example custom configs
samples/       inputs/ (4 sources + malformed + empty) and expected/ gold profiles
src/eightfold/ models, detect, sources/, normalize/, resolve, fuse, confidence,
               project, validate, pipeline, cli, llm/
web/app.py     minimal stdlib UI
tests/         normalize, projection, fusion, e2e, edge cases
docs/design.md the one-page technical design
```
