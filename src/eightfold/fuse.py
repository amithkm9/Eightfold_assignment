"""Fusion / survivorship: one entity cluster -> one CanonicalProfile.

Conflict resolution is per-field-CLASS, not one global rule:
  - multi-valued (emails, phones, skills, links) -> UNION + dedup (never drop a real value)
  - identity/contact (name, location, headline)  -> WINNER by source-trust priority,
        tie-broken by extracted confidence then completeness (all deterministic)
  - derived (years_experience)                   -> prefer an explicit statement, else
        compute from CLOSED experience ranges, MERGING overlapping spans so concurrent
        roles aren't double-counted (and never using "present", so the result is
        independent of today's date -> determinism preserved)
  - list-of-records (experience, education)      -> dedup by identity, keep the richest

Provenance and confidence are emitted for every populated field.
"""

from __future__ import annotations

from collections import OrderedDict, defaultdict
from typing import Any

from ._text import name_key, slug
from .confidence import priority, score_field, score_overall
from .models import (
    CanonicalProfile,
    Claim,
    Education,
    Experience,
    Links,
    Location,
    ProvenanceEntry,
    Skill,
    SourceRecord,
)
from .normalize.dates import YYYY_MM

# Provenance / output ordering — fixed for deterministic output.
_FIELD_ORDER = ["full_name", "emails", "phones", "location", "links", "headline",
                "years_experience", "skills", "experience", "education"]
_PROV_TAIL = len(_FIELD_ORDER)  # sort sentinel for any field not in the list


def _vlen(v: Any) -> int:
    return len(str(v)) if v is not None else 0


def _best(claims: list[Claim]) -> Claim:
    """Deterministic winner: source priority -> extractor confidence -> normalized ->
    longer value -> lexical (stable)."""
    return max(claims, key=lambda c: (priority(c.source), c.extracted_confidence,
                                      c.normalized_ok, _vlen(c.value), str(c.value)))


def fuse(cluster: list[SourceRecord], index: int = 0) -> CanonicalProfile:
    claims_by_field: dict[str, list[Claim]] = defaultdict(list)
    for rec in cluster:
        for c in rec.claims:
            if c.value not in (None, ""):
                claims_by_field[c.field].append(c)

    field_conf: dict[str, float] = {}
    prov: list[ProvenanceEntry] = []

    def add_prov(field: str, claim: Claim) -> None:
        prov.append(ProvenanceEntry(field=field, source=claim.source, method=claim.method))

    def union_sources(claims: list[Claim]) -> list[str]:
        """Distinct sources, ordered by trust priority (deterministic)."""
        return sorted({c.source for c in claims}, key=lambda s: -priority(s))

    def add_prov_per_source(field: str, claims: list[Claim]) -> None:
        for src in union_sources(claims):
            add_prov(field, next(c for c in claims if c.source == src))

    # ---- full_name: winner by trust, conflict-aware confidence ----
    full_name = None
    if claims_by_field.get("full_name"):
        cands = claims_by_field["full_name"]
        winner = _best(cands)
        full_name = winner.value
        agree_sources = len({c.source for c in cands if name_key(c.value) == name_key(winner.value)})
        conflicted = len({name_key(c.value) for c in cands}) > 1
        field_conf["full_name"] = score_field(winner, agree_sources, conflicted)
        add_prov("full_name", winner)

    # ---- emails / phones: union + dedup ----
    def union_field(field: str, key_fn) -> list[str]:
        claims = claims_by_field.get(field, [])
        if not claims:
            return []
        field_conf[field] = score_field(_best(claims), len(union_sources(claims)), False)
        add_prov_per_source(field, claims)
        return sorted({key_fn(c.value) for c in claims})

    emails = union_field("emails", lambda v: str(v).strip().lower())
    phones = union_field("phones", lambda v: str(v).strip())

    # ---- location: pick best, then backfill missing subfields by trust ----
    location = Location()
    if claims_by_field.get("location"):
        cands = claims_by_field["location"]
        ordered = sorted(cands, key=lambda c: (priority(c.source), c.extracted_confidence), reverse=True)
        merged = {"city": None, "region": None, "country": None}
        for c in ordered:
            if isinstance(c.value, dict):
                for k in merged:
                    if not merged[k] and c.value.get(k):
                        merged[k] = c.value[k]
        location = Location(**merged)
        field_conf["location"] = score_field(ordered[0], len(union_sources(cands)), False)
        add_prov("location", ordered[0])

    # ---- links: per-subtype winner; github is authoritative for its own URL ----
    def pick(field: str) -> Claim | None:
        claims = claims_by_field.get(field, [])
        return _best(claims) if claims else None

    linkedin_w, github_w, portfolio_w = pick("links.linkedin"), pick("links.github"), pick("links.portfolio")
    others = sorted({str(c.value) for c in claims_by_field.get("links.other", [])})
    links = Links(linkedin=linkedin_w.value if linkedin_w else None,
                  github=github_w.value if github_w else None,
                  portfolio=portfolio_w.value if portfolio_w else None, other=others)
    link_claims = (claims_by_field.get("links.linkedin", []) + claims_by_field.get("links.github", [])
                   + claims_by_field.get("links.portfolio", []))
    if link_claims:
        for w in (linkedin_w, github_w, portfolio_w):
            if w is not None:
                add_prov("links", w)
        field_conf["links"] = score_field(_best(link_claims), 1, False)

    # ---- headline: winner by trust ----
    headline = None
    if claims_by_field.get("headline"):
        cands = claims_by_field["headline"]
        winner = _best(cands)
        headline = winner.value
        field_conf["headline"] = score_field(winner, 1, len({c.value for c in cands}) > 1)
        add_prov("headline", winner)

    # ---- skills: union by canonical name; per-skill confidence + sources ----
    skills: list[Skill] = []
    if claims_by_field.get("skills"):
        by_name: OrderedDict[str, list[Claim]] = OrderedDict()
        for c in sorted(claims_by_field["skills"], key=lambda c: str(c.value)):
            by_name.setdefault(c.value, []).append(c)
        for sk_name, cs in by_name.items():
            srcs = union_sources(cs)
            skills.append(Skill(name=sk_name, confidence=score_field(_best(cs), len(srcs), False), sources=srcs))
        field_conf["skills"] = round(sum(s.confidence for s in skills) / len(skills), 4)
        add_prov_per_source("skills", claims_by_field["skills"])

    # ---- experience: dedup by (company,title), keep richest ----
    experience: list[Experience] = []
    if claims_by_field.get("experience"):
        exp_by_key: OrderedDict[tuple, tuple[Claim, int]] = OrderedDict()
        for c in claims_by_field["experience"]:
            v = c.value if isinstance(c.value, dict) else {}
            key = ((v.get("company") or "").lower().strip(), (v.get("title") or "").lower().strip())
            completeness = sum(1 for k in ("start", "end", "summary") if v.get(k))
            if key not in exp_by_key or completeness > exp_by_key[key][1]:
                exp_by_key[key] = (c, completeness)
        for c, _ in exp_by_key.values():
            v = c.value if isinstance(c.value, dict) else {}
            experience.append(Experience(**{k: v.get(k) for k in
                                            ("company", "title", "start", "end", "summary")}))
        experience.sort(key=lambda e: (e.start or ""), reverse=True)
        field_conf["experience"] = score_field(_best(claims_by_field["experience"]),
                                               len(union_sources(claims_by_field["experience"])), False)
        add_prov_per_source("experience", claims_by_field["experience"])

    # ---- education: dedup by institution ----
    education: list[Education] = []
    if claims_by_field.get("education"):
        edu_by_inst: OrderedDict[str, Claim] = OrderedDict()
        for c in claims_by_field["education"]:
            v = c.value if isinstance(c.value, dict) else {}
            inst = (v.get("institution") or "").lower().strip()
            if inst and inst not in edu_by_inst:
                edu_by_inst[inst] = c
        education = [Education(**{k: c.value.get(k) for k in
                                 ("institution", "degree", "field", "end_year")})
                     for c in edu_by_inst.values()]
        field_conf["education"] = score_field(_best(claims_by_field["education"]), 1, False)
        add_prov_per_source("education", claims_by_field["education"])

    # ---- years_experience: prefer stated, else compute from MERGED closed spans ----
    years_experience = None
    if claims_by_field.get("years_experience"):
        winner = _best(claims_by_field["years_experience"])
        years_experience = float(winner.value)
        field_conf["years_experience"] = score_field(winner, 1, False)
        add_prov("years_experience", winner)
    else:
        computed = _years_from_experience(experience)
        if computed is not None:
            years_experience = computed
            field_conf["years_experience"] = 0.7  # derived: moderate confidence

    # ---- candidate_id: deterministic; uniqueness enforced by the pipeline ----
    if full_name:
        cid = slug(full_name)
    elif emails:
        cid = slug(emails[0].split("@")[0])
    else:
        cid = f"candidate-{index:04d}"
    cid = cid or f"candidate-{index:04d}"

    # Stable provenance order, then drop exact duplicates.
    prov.sort(key=lambda p: (_FIELD_ORDER.index(p.field) if p.field in _FIELD_ORDER else _PROV_TAIL,
                             -priority(p.source)))
    seen: set[tuple] = set()
    deduped: list[ProvenanceEntry] = []
    for p in prov:
        pkey = (p.field, p.source, p.method)
        if pkey not in seen:
            seen.add(pkey)
            deduped.append(p)

    return CanonicalProfile(
        candidate_id=cid, full_name=full_name, emails=emails, phones=phones,
        location=location, links=links, headline=headline,
        years_experience=years_experience, skills=skills, experience=experience,
        education=education, provenance=deduped,
        overall_confidence=score_overall(field_conf), field_confidence=field_conf,
    )


def _months(ym: str) -> int:
    y, m = ym.split("-")
    return int(y) * 12 + int(m)


def _years_from_experience(experience: list[Experience]) -> float | None:
    """Total tenure in years across CLOSED spans, merging overlapping intervals so
    concurrent roles aren't double-counted. Open/current spans are skipped to keep the
    result independent of today's date (determinism)."""
    intervals: list[tuple[int, int]] = []
    for e in experience:
        if e.start and e.end and YYYY_MM.match(e.start) and YYYY_MM.match(e.end):
            s, t = _months(e.start), _months(e.end)
            if t > s:
                intervals.append((s, t))
    if not intervals:
        return None
    intervals.sort()
    merged: list[list[int]] = [list(intervals[0])]
    for s, t in intervals[1:]:
        if s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], t)
        else:
            merged.append([s, t])
    return round(sum(t - s for s, t in merged) / 12, 1)
