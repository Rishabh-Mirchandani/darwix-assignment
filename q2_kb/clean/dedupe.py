"""Exact and near-duplicate removal.

Why this matters more than it sounds: insurance sites repeat the same benefit
copy across every plan page, and the same FAQ answer under several categories.
Left alone, the top-4 retrieval slots fill with four phrasings of one fact, the
agent sees no new information, and genuinely relevant records get pushed out.
Deduplication is a *recall* fix, not just a storage saving.

Two passes:

1. **Exact** -- SHA-256 over normalised text. Cheap, catches syndicated blocks.
2. **Near** -- MinHash + LSH over word 3-shingles (datasketch). Catches the
   "covers 10,000+ hospitals" / "covers 10,000 plus hospitals" class of
   variation that exact hashing misses.

When duplicates are found we keep a *canonical* record rather than deleting
blindly: the longest body wins (most complete statement of the rule), and the
discarded URLs are retained on the survivor as `duplicate_of` provenance so the
audit trail survives.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from datasketch import MinHash, MinHashLSH

SHINGLE_SIZE = 3
NEAR_DUP_THRESHOLD = 0.82   # Jaccard; tuned below for insurance boilerplate
MINHASH_PERMS = 128


def normalise_for_hash(text: str) -> str:
    """Aggressive normalisation used only for comparison, never for storage."""
    t = text.lower()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def exact_hash(text: str) -> str:
    return hashlib.sha256(normalise_for_hash(text).encode()).hexdigest()


def shingles(text: str, size: int = SHINGLE_SIZE) -> set[str]:
    words = normalise_for_hash(text).split()
    if len(words) < size:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i: i + size]) for i in range(len(words) - size + 1)}


def minhash(text: str) -> MinHash:
    m = MinHash(num_perm=MINHASH_PERMS)
    for sh in shingles(text):
        m.update(sh.encode())
    return m


@dataclass
class DedupeResult:
    kept: list[int] = field(default_factory=list)          # surviving indices
    dropped: list[int] = field(default_factory=list)
    # survivor index -> the indices it absorbed
    merged_into: dict[int, list[int]] = field(default_factory=dict)
    exact_dupes: int = 0
    near_dupes: int = 0

    @property
    def summary(self) -> dict[str, int]:
        return {
            "input": len(self.kept) + len(self.dropped),
            "kept": len(self.kept),
            "dropped": len(self.dropped),
            "exact_duplicates": self.exact_dupes,
            "near_duplicates": self.near_dupes,
        }


def deduplicate(texts: list[str]) -> DedupeResult:
    """Return which indices survive, and what each survivor absorbed."""
    result = DedupeResult()
    n = len(texts)
    if n == 0:
        return result

    # ---- pass 1: exact ----
    by_hash: dict[str, list[int]] = {}
    for i, t in enumerate(texts):
        by_hash.setdefault(exact_hash(t), []).append(i)

    survivors_after_exact: list[int] = []
    for group in by_hash.values():
        # Longest body is the most complete statement of the rule.
        winner = max(group, key=lambda i: len(texts[i]))
        survivors_after_exact.append(winner)
        losers = [i for i in group if i != winner]
        if losers:
            result.merged_into.setdefault(winner, []).extend(losers)
            result.dropped.extend(losers)
            result.exact_dupes += len(losers)

    survivors_after_exact.sort()

    # ---- pass 2: near-duplicate via LSH ----
    lsh = MinHashLSH(threshold=NEAR_DUP_THRESHOLD, num_perm=MINHASH_PERMS)
    signatures: dict[int, MinHash] = {}
    final: list[int] = []

    for idx in survivors_after_exact:
        text = texts[idx]
        # Very short blocks produce unstable signatures; keep them untouched.
        if len(normalise_for_hash(text).split()) < SHINGLE_SIZE * 3:
            final.append(idx)
            continue

        sig = minhash(text)
        matches = lsh.query(sig)
        if matches:
            # Attach to the first match whose body is at least as long;
            # otherwise this record supersedes it.
            target = matches[0]
            if len(texts[idx]) > len(texts[target]):
                # New record is more complete: swap roles.
                lsh.remove(target)
                final = [i for i in final if i != target]
                result.merged_into.setdefault(idx, []).append(target)
                result.merged_into[idx].extend(result.merged_into.pop(target, []))
                result.dropped.append(target)
                lsh.insert(idx, sig)
                signatures[idx] = sig
                final.append(idx)
            else:
                result.merged_into.setdefault(target, []).append(idx)
                result.dropped.append(idx)
            result.near_dupes += 1
        else:
            lsh.insert(idx, sig)
            signatures[idx] = sig
            final.append(idx)

    result.kept = sorted(set(final))
    result.dropped = sorted(set(result.dropped))
    return result
