# Eightfold — Canonical Candidate Profile Transformer · One-Page Design

> **Thesis.** Build one *rich, provenance-carrying **canonical record*** on the **write side**, then a
> *pure, deterministic **projection + validation** layer* on the **read side** — **CQRS for candidate
> data**. The canonical record is computed **once** and is config-agnostic; the runtime config is
> **data fed to a generic projector**, never logic in the merge engine.
> **Governing value:** *wrong-but-confident is worse than honestly-empty* → never invent; abstain to `null`.

---

## Architecture (write once → project per request)

```
                                  ┌──────────  WRITE SIDE — built ONCE, config-agnostic  ──────────┐
  Recruiter CSV ─┐
  ATS JSON       ├─ detect ─► extract ─► normalize ─► resolve ─► fuse ─►  ╔═══════════════════════╗
  GitHub (fixt.) ┤            (→Claim)   e164·YYYY-MM  entity-   survivor- ║   CanonicalProfile     ║
  Recruiter .txt ┘                       ·ISO·skills    match     ship     ║  one deduped, fully    ║
                                                                           ║  provenance-tagged rec ║
        every value enters as a  Claim{ source · method · raw_span ·       ╚═══════════╤═══════════╝
        confidence } → provenance & confidence are invariants from birth               │
                                                                                       ▼
   runtime config ─(data, not code)─────────────────────────────►  ╔═══ READ SIDE — PURE fn ═══╗
   { fields[], from-paths, normalize, on_missing,                   ║  project → validate       ║
     include_provenance/confidence }                                ║  (Canonical, Config)→JSON ║
                                                                    ╚═══════════════════════════╝
                                                          → default schema  OR  custom-shaped output
```

**Pipeline contract:** `detect → extract → normalize → resolve → fuse → project → validate`.
Each source extractor returns a `Result` (failure-as-value), so a missing/garbage source degrades to a
status and **never crashes the run**.

---

## Canonical schema & normalized formats

`candidate_id, full_name, emails[], phones[], location{city,region,country}, links{linkedin,github,
portfolio,other[]}, headline, years_experience, skills[{name,confidence,sources[]}],
experience[{company,title,start,end,summary}], education[{institution,degree,field,end_year}],
provenance[{field,source,method}], overall_confidence`.

| Field type | Normalized to | Tool |
|---|---|---|
| phones | **E.164** | `phonenumbers` |
| dates | **YYYY-MM** (year-only kept; month never fabricated) | `dateutil` + month-token guard |
| country | **ISO-3166 alpha-2** | `pycountry` |
| skills | **canonical name** | swappable synonym map |

The *same* normalizer registry is reused by the projection layer, so a value is normalized identically
whichever path produced it.

---

## Merge / conflict resolution & confidence

Per-field-**class** survivorship — deterministic (stable sort + fixed priority list):

- **Multi-valued** (emails, phones, skills, links) → **union + dedup** — never drop a real value.
- **Identity/contact** (name, location, headline) → **winner by source-trust priority**
  `csv > ats > linkedin > github > resume > notes`, tie-broken by extractor confidence → completeness;
  conflicts retained in provenance.
- **Authority override** → GitHub is canonical for its own URL & languages.
- **Derived** (`years_experience`) → prefer a stated value, else compute from **closed** spans with
  **overlapping intervals merged** (so concurrent roles aren't double-counted; "present" skipped so the
  result never depends on today's date → determinism).

**Confidence (explainable):**
`clamp(source_trust × method_factor × normalize_factor + corroboration − conflict, 0, 1)`;
`overall_confidence` = mean of resolved field scores. Every number traces to its inputs.

**Entity resolution:** union-find over **strong keys** (email, E.164 phone, github/linkedin URL) +
composite `name+company` (only when both present), then a **guarded fuzzy pass** —
*shared last-name/portfolio-host block → name-compatible → ≥1 corroborating signal → no conflicting
strong id* — so a GitHub display name with no email still merges, while two different emails never do.
**Blocking is implemented** (bucketed, with a size guard) → near-linear: **~10k records in <0.4s**.

---

## The runtime config — the "required twist"

A **pure** function `(CanonicalProfile, Config) → Output`:
**select** fields · **remap** via a `from` path DSL (`location.country`, `emails[0]`, `skills[].name`) ·
**normalize** per field · **toggle** provenance/confidence · **`on_missing` ∈ {null, omit, error}**.
The projected output is then **validated against the requested schema**. Purity ⇒ deterministic ⇒
trivially testable. *Same engine, no code changes.*

---

## LLM vs determinism (the deliberate differentiator)

Unstructured prose invites an LLM, but the brief demands *deterministic* + *never-invented*. Resolution:
rules win on identity/contact; **Claude (`claude-haiku-4-5`, structured JSON output) only proposes
low-confidence `llm_extract` claims** that can never overwrite a structured value; calls are **cached by
input-hash** for reproducibility (the model takes no temperature param, so caching — not `temp=0` — is the
determinism guarantee); the model is instructed to **abstain** rather than guess. Off by default.
Haiku is the cheapest tier and ample for this bounded extraction — Opus/Sonnet headroom isn't needed.

---

## Edge cases handled

1. **Conflicting names** (CSV "Jane Doe" vs GitHub "Jane Q. Doe") → trust picks CSV; both kept in provenance.
2. **Same person, 3 phone formats** → match on email, union after E.164 dedup → one number.
3. **Garbage / empty source** (malformed JSON, empty `.txt`) → isolated `failed`/`empty` status; run continues.
4. **Un-normalizable value** ("call me!") → `null` + `method=normalize_failed`, low confidence — honest, not invented.
5. **Required field unresolved + `on_missing:"error"`** → clean per-candidate error in `errors[]`, never a batch crash.

---

## Deliberately descoped (honest scope under time pressure)

Cross-machine/**sharded** resolution & ML-learned matching (single-node blocking + guarded fuzzy
*implemented*); full streaming for multi-million-row corpora (`--jsonl` offered); exhaustive skill
ontology (small swappable map); resume PDF/DOCX layout parsing (notes/`.txt` covers prose);
auth/multi-tenancy.
**Rationale:** maximize the graded core — canonical↔projection separation, provenance/confidence,
honest-null — over breadth.
