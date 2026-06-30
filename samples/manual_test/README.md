# Manual test corpus — edge-case coverage

A hand-built corpus of **12 people across 4 source types** plus deliberate
robustness artifacts, designed so you can eyeball whether the pipeline reconciles
and normalizes correctly. Run it with:

```bash
eightfold run --inputs samples/manual_test --out out.json                 # full canonical schema
eightfold run --inputs samples/manual_test --config config/example_custom.json --out out_custom.json
```

> This `README.md` is itself the **"unknown file type is skipped"** case — the engine
> only ingests `.csv` / `.json` / `.txt`, so it silently ignores this file.

## Files

```
recruiter.csv           Carlos, Wei(Acme), Wei(Globex), Aisha, Robert, + a no-name row
ats.json                Carlos, Aisha, Diego, Mei, Sofia  (foreign field names)
github/carlosr.json     Carlos        github/meilin.json    Mei
github/tombaker.json    Tom           github/fatima.json    Fatima
notes/carlos.txt        Carlos        notes/tom.txt         Tom
notes/robert.txt        Robert        notes/fatima.txt      Fatima
notes/john-a.txt        John Smith #1 notes/john-b.txt      John Smith #2
notes/empty.txt         (empty source → reported "empty")
malformed.json          (invalid JSON → reported "failed", run continues)
```

## Expected candidates (default schema)

You should get **11 candidates** (Carlos, 2× Wei Chen, Aisha, Robert, `noname`,
Diego, Mei, Sofia, Tom, 2× John Smith, Fatima = 13 — see note) and **0 errors** on
the default schema. On the **custom config** the `noname` row becomes **1 error**
(`required field 'full_name' is missing`) and drops out.

| # | Person | Sources | What it verifies |
|---|--------|---------|------------------|
| 1 | **Carlos Rivera** | csv + ats + github + notes | Full 4-way merge. Name survivorship (`Carlos Rivera` from CSV beats GitHub's `Carlos A. Rivera`). Phone: 3 formats → one `+16195550199`. Email union. Skill canonicalization + corroboration (`Go`, `Python` from 2 sources). **GitHub US-state location `San Diego, CA` → `region: "CA"`, `country: null`** (not Canada). Dates `March 2021`→`2021-03`, `2019` kept year-only, `present`→open. Stated years `9.0`. Notes `Candidate:` label read. |
| 2 | **Wei Chen** (Acme) | csv | Same name as #3, **different email** → must stay **separate**. |
| 3 | **Wei Chen** (Globex) | csv | Conflicting strong ID (different email) blocks the merge. Both get **unique `candidate_id`s** (`wei-chen`, `wei-chen-<hash>`). |
| 4 | **Aisha Khan** | csv + ats | **Honest-null:** CSV phone `"call me later"` is unparseable → `phones: []` (never invented). ATS country `"Atlantis"` is unknown → `location.country: null`. |
| 5 | **Diego Santos** | ats | **Date edges, no fabricated month:** `Summer 2019`→`2019`, `circa 2015`→`2015`, `2018-2020` range → year only (never `-01`), `present`→open. `years_experience` computed from the **only** fully-dated closed span (`2016-03..2019-06`) ⇒ **3.25**. |
| 6 | **Mei Lin** | ats + github | Merges via name + shared `Go` (no shared email). Skill canonicalization: `golang→Go`, `k8s→Kubernetes`, `javascript→JavaScript`; unknown skills kept & title-cased (`elasticsearch→Elasticsearch`, `distributed systems→Distributed Systems`). GitHub `Seattle, WA` → `region: "WA"`, `country: null`. |
| 7 | **Tom Baker** | github + notes | GitHub **authoritative** for its own link + languages. Foreign location `Bengaluru, India` → **`country: "IN"`** (a real country — contrast with #1/#6 US-state abbreviations). |
| 8 | **Sofia Rossi** | ats | **Derived years from overlapping spans:** `2015-01..2018-01` ∪ `2018-01..2020-01`, with `2016-01..2017-01` subsumed (not double-counted) ⇒ **5.0**. |
| 9 | **Robert Lang** | csv + notes | Notes `Name: Robert Lang` (capitalized label is read). `Username: rlang99` is **not** mistaken for the name. `Employee ID 12345` (too few digits) is **not** matched as a phone. |
| 10 | **John Smith** #1 | notes | Same name as #11, no strong key, **different skills** → must stay **separate**. |
| 11 | **John Smith** #2 | notes | Name alone is insufficient to merge. Both get **unique `candidate_id`s** via a content fingerprint. |
| 12 | **Fatima Noor** | github + notes | Merges with **no shared email/phone** — via shared portfolio host `fatima.codes`. `LinkedIn` URL extracted from notes. GitHub `Toronto, Canada` → `country: "CA"` (full country name resolves; contrast with the bare `CA` abbreviation in #1). |
| — | **noname row** | csv | No-name CSV row → `candidate_id: "noname"`, `full_name: null` (honest-null). Becomes a **`required field` error** under the custom config and is isolated, not fatal. |
| — | **malformed.json** | file | Invalid JSON → reported `status: "failed"`; the batch keeps going. |
| — | **empty.txt** | file | 0-byte source → reported `status: "empty"`. |

> Note on count: 13 profiles total (the 12 numbered + `noname`). "11 candidates / 0 errors"
> above is approximate — confirm the exact set yourself; the point is *which* merge and
> *which* stay separate, per the table.

## Determinism check
Run twice into two files and `diff` them — output is byte-identical.
