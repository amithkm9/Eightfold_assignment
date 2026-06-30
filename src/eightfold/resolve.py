"""Entity resolution: group SourceRecords that describe the same person.

Deterministic, layered match keys (no randomness, no ML at runtime):
  - strong identity keys: email, E.164 phone, github/linkedin URL  -> certain match.
    Name+company is deliberately NOT a strong key — two different people who share a
    common name and employer must never be force-merged.
  - guarded fuzzy pass: when no strong key is shared (e.g. a GitHub profile with a
    display name "Jane Q. Doe" and no email), merge two clusters ONLY when they share
    a block (last-name token OR personal portfolio host), their names are compatible,
    at least one corroborating signal agrees (shared skill / city / host), AND they do
    not carry CONFLICTING strong identifiers (different emails/phones/URLs).

Scale: the fuzzy pass uses BLOCKING — clusters are bucketed by last-name token /
portfolio host and only compared within a bucket (skipping pathologically large
buckets), so it is near-linear in practice rather than O(n^2) all-pairs. Merges are
applied via union-find, so the result is independent of comparison order.

Known limitation: the conflicting-identifier guard is evaluated PAIRWISE on the two
clusters being compared, not against the whole accumulated union-find component, so a
transitive chain A-B-C could in principle merge where A and C carry conflicting strong
ids. Bounded by blocking and rare on real data; component-level conflict checking is
future work.
"""

from __future__ import annotations

import difflib
from collections import defaultdict
from typing import TypedDict

from ._text import name_key, norm_url, url_host
from .models import SourceRecord

# Hosts that many unrelated people share -> not safe as an identity/block key.
_SHARED_HOSTS = {
    "medium.com", "dev.to", "github.io", "wordpress.com", "blogspot.com",
    "substack.com", "notion.site", "gitlab.io", "vercel.app", "netlify.app",
}
_NAME_SIM_THRESHOLD = 0.9
_MAX_BUCKET = 200  # skip fuzzy comparison inside buckets larger than this (e.g. very common surnames)


def identity_keys(rec: SourceRecord) -> set[str]:
    """*Strong* identity keys derived from a record's normalized claims.

    Only unambiguous identifiers (email, E.164 phone, github/linkedin URL) become
    hard-union keys. Name+company is deliberately NOT a key: two different people who
    share a common name and employer (e.g. two "Michael Chen" at Amazon) must never be
    force-merged. That case is left to the guarded fuzzy pass, which can reject it on a
    conflicting strong identifier.
    """
    keys: set[str] = set()
    for c in rec.claims:
        if c.value in (None, ""):
            continue
        if c.field == "emails":
            keys.add(f"email:{str(c.value).strip().lower()}")
        elif c.field == "phones":
            keys.add(f"phone:{c.value}")  # already E.164
        elif c.field == "links.github":
            keys.add(f"gh:{norm_url(c.value)}")
        elif c.field == "links.linkedin":
            keys.add(f"li:{norm_url(c.value)}")
    return keys


class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)  # min-root keeps grouping deterministic


def _group(uf: _UnionFind, n: int) -> list[list[int]]:
    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        groups[uf.find(i)].append(i)
    return [groups[root] for root in sorted(groups)]


def resolve(records: list[SourceRecord]) -> list[list[SourceRecord]]:
    """Cluster records into entities. Returns clusters in first-appearance order."""
    uf = _UnionFind(len(records))
    key_owner: dict[str, int] = {}
    for i, rec in enumerate(records):
        for k in identity_keys(rec):
            if k in key_owner:
                uf.union(i, key_owner[k])
            else:
                key_owner[k] = i
    clusters = [[records[i] for i in idxs] for idxs in _group(uf, len(records))]
    return _fuzzy_merge(clusters)


class _Signals(TypedDict):
    names: set[str]
    skills: set[str]
    cities: set[str]
    hosts: set[str]
    last: set[str]
    emails: set[str]
    phones: set[str]
    gh: set[str]
    li: set[str]


def _signals(cluster: list[SourceRecord]) -> _Signals:
    """Cheap, deterministic signals used by the guarded fuzzy pass."""
    s: _Signals = {k: set() for k in _Signals.__annotations__}  # type: ignore[assignment]
    for rec in cluster:
        for c in rec.claims:
            if c.value in (None, ""):
                continue
            if c.field == "full_name":
                nk = name_key(c.value)
                s["names"].add(nk)
                toks = nk.split()
                if toks:
                    s["last"].add(toks[-1])
            elif c.field == "skills":
                s["skills"].add(str(c.value))
            elif c.field == "location" and isinstance(c.value, dict) and c.value.get("city"):
                s["cities"].add(str(c.value["city"]).lower())
            elif c.field == "links.portfolio":
                h = url_host(c.value)
                if h and h not in _SHARED_HOSTS:
                    s["hosts"].add(h)
            elif c.field == "emails":
                s["emails"].add(str(c.value).strip().lower())
            elif c.field == "phones":
                s["phones"].add(str(c.value))
            elif c.field == "links.github":
                s["gh"].add(norm_url(c.value))
            elif c.field == "links.linkedin":
                s["li"].add(norm_url(c.value))
    return s


def _name_compatible(a: set[str], b: set[str]) -> bool:
    for na in a:
        for nb in b:
            ta, tb = set(na.split()), set(nb.split())
            if not ta or not tb:
                continue
            if ta <= tb or tb <= ta:  # subset handles middle initials / partial names
                return True
            if difflib.SequenceMatcher(None, na, nb).ratio() >= _NAME_SIM_THRESHOLD:
                return True
    return False


def _should_merge(a: _Signals, b: _Signals) -> bool:
    # Conflicting strong identifiers => definitely different people. (If they shared a
    # strong key they'd already be unioned, so non-empty-and-disjoint means conflict.)
    for kind in ("emails", "phones", "gh", "li"):
        if a[kind] and b[kind] and a[kind].isdisjoint(b[kind]):
            return False
    in_block = bool(a["last"] & b["last"]) or bool(a["hosts"] & b["hosts"])
    if not in_block or not _name_compatible(a["names"], b["names"]):
        return False
    return bool(a["skills"] & b["skills"]) or bool(a["cities"] & b["cities"]) or bool(a["hosts"] & b["hosts"])


def _fuzzy_merge(clusters: list[list[SourceRecord]]) -> list[list[SourceRecord]]:
    n = len(clusters)
    if n < 2:
        return clusters
    sigs = [_signals(c) for c in clusters]

    # Blocking: bucket clusters by last-name token and portfolio host; only compare
    # within a bucket. This bounds the comparison count instead of all-pairs O(n^2).
    buckets: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, s in enumerate(sigs):
        for tok in s["last"]:
            buckets[("ln", tok)].append(i)
        for host in s["hosts"]:
            buckets[("host", host)].append(i)

    uf = _UnionFind(n)
    compared: set[tuple[int, int]] = set()
    for members in buckets.values():
        if len(members) > _MAX_BUCKET:
            continue  # avoid quadratic blow-up on huge common-surname buckets
        for x in range(len(members)):
            for y in range(x + 1, len(members)):
                i, j = members[x], members[y]
                pair = (i, j) if i < j else (j, i)
                if pair in compared:
                    continue
                compared.add(pair)
                if uf.find(i) != uf.find(j) and _should_merge(sigs[i], sigs[j]):
                    uf.union(i, j)

    merged: list[list[SourceRecord]] = []
    for idxs in _group(uf, n):
        combined: list[SourceRecord] = []
        for i in idxs:
            combined.extend(clusters[i])
        merged.append(combined)
    return merged
