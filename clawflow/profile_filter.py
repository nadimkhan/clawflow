"""Quality filter: drop leads that don't match the user's profile.

The user's profile (parsed from clawflow.yml) is the source of truth for
what counts as a "worth pursuing" lead. This module exposes a single
function used by the scheduler right after the fetch phase.

Filters dropped:
  * Excluded stack keywords (case-insensitive substring match on the
    concatenated title+tags+raw-text blob)
  * Scam signals (substring match against the title)
  * Out-of-band seniority hints (e.g. "intern", "junior dev") UNLESS the
    user's preferred seniority matches them
  * Below-budget leads (numeric hints like "$200/mo", "minimum $500")

Pure function. No network. No I/O. Easy to unit test.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Iterable

from clawflow.leads import Lead

log = logging.getLogger("clawflow.profile_filter")

# What seniority words trigger a drop in each band. Empty = no auto-drop.
# Senior users won't see "intern/junior" jobs. Junior users will.
SENIOR_DROP_HINTS: dict[str, tuple[str, ...]] = {
    "principal": ("intern", "junior", "entry level", "graduate program", "new grad"),
    "senior":    ("intern", "junior", "entry level", "graduate program", "new grad"),
    "mid":       ("intern", "graduate program", "new grad"),
    "junior":    ("principal", "staff"),
    "intern":    ("principal", "staff", "senior", "mid"),
}


def _join_haystack(lead: Lead) -> str:
    parts = [lead.title or ""]
    raw = lead.raw or {}
    if isinstance(raw, dict):
        # Each source keeps a different descriptive field; we don't know which
        # one carries the readable excerpt so accept any of the common names.
        for key in (
            "summary", "body", "excerpt", "page_excerpt", "details",
            "description", "text", "payload_dump",
        ):
            val = raw.get(key)
            if val:
                parts.append(str(val))
        # Last resort: dump any string-typed raw values if we still have no
        # body text to look at.
        if len(parts) == 1 and len(parts[0]) < 50:
            for v in raw.values():
                if isinstance(v, str) and len(v) > 50:
                    parts.append(v)
                    break
    # Defensive cleanup: strip anything that looks like a CSS / JS block;
    # careers pages often serve a few KB of inline CSS that pollutes
    # keyword matching.
    text = "\n".join(parts)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"@font-face\s*{[^}]*}", " ", text)  # drop face-font decls
    text = re.sub(r"\.([a-zA-Z-]+)\s*{[^}]*}", " ", text)  # drop CSS class blocks
    text = re.sub(r"\s+", " ", text)
    return text.lower()


def _budget_hints_usd(haystack: str) -> list[int]:
    """Extract numeric USD-ish hints from text. Roughly: $5k, $2k/mo, etc."""
    hits: list[int] = []
    for m in re.finditer(r"\$\s*(\d{1,4}(?:[,\.]\d{3})?)(?:\s*(k|m|million)?)\b", haystack, re.IGNORECASE):
        raw = m.group(1).replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            continue
        suffix = (m.group(2) or "").lower()
        if suffix in ("k",):
            value *= 1_000
        elif suffix in ("m", "million"):
            value *= 1_000_000
        if value > 0:
            hits.append(int(value))
    return hits


def _stack_keep(haystack: str, keep_terms: Iterable[str]) -> bool:
    """Decide whether ``haystack`` matches the user's preferred-stack profile.

    Two paths to acceptance:
      1. Direct: at least one preferred-stack term substring-matches.
      2. Indirect ("fallback"): at least 2 distinct role-hint words appear
         in the haystack (e.g. "engineer", "smart contract", "ai agent").
         Useful when careers pages describe roles without naming stacks.

    If ``keep_terms`` is empty, accept everything.
    """
    if not keep_terms:
        return True
    for t in keep_terms:
        if t and t.lower() in haystack:
            return True
    # Fallback: careers pages often don't list stack names explicitly.
    # If the page mentions multiple distinct role-hint words, accept.
    role_hints = {
        # Roles
        "engineer", "developer", "researcher", "designer",
        "architect", "specialist", "manager",
        # Tech keywords that show up in careers even without explicit "stack" labels
        "smart contract", "solidity", "rust", "audit",
        "ai", "llm", "agent", "protocol",
        "growth", "marketing", "devrel", "lead",
        "product", "research", "defi", "zk",
        "frontend", "backend", "infrastructure",
        "reliability", "security", "data",
        # Generic "we're hiring" signals
        "hiring", "join us", "join our", "we're hiring",
        "apply", "remote",
        # DAO/company role nouns
        "founder", "contributor", "engineer", "ops",
    }
    hits = 0
    seen_words = set()
    for w in role_hints:
        if w in haystack and w not in seen_words:
            hits += 1
            seen_words.add(w)
            if hits >= 2:
                return True
    return False


def _stack_block(haystack: str, block_terms: Iterable[str]) -> bool:
    """If block_terms is empty, block nothing. Otherwise any hit -> drop."""
    for t in block_terms:
        if t and t.lower() in haystack:
            return True
    return False


def apply_profile_filter(lead: Lead, profile: Any) -> tuple[bool, str]:
    """Return (keep, reason_if_dropped).

    ``profile`` is a clawflow.config.ProfileConfig instance.
    Caller should drop the lead if keep is False.
    """
    hay = _join_haystack(lead)

    # 1. Excluded stacks
    if _stack_block(hay, getattr(profile, "excluded_stacks", ())):
        return False, "excluded_stack"

    # 2. Preferred stacks (when non-empty)
    keep_terms = list(getattr(profile, "preferred_stacks", ()) or ())
    if not _stack_keep(hay, keep_terms):
        return False, "no_preferred_stack_match"

    # 3. Scam signals
    sl = (getattr(profile, "scam_signals", ()) or ())
    for s in sl:
        if s and s.lower() in hay:
            return False, f"scam_signal:{s}"

    # 4. Seniority drop hints (skip if user's seniority is IN the hint band)
    user_seniority = (getattr(profile, "seniority", "senior") or "senior").lower()
    drop_words = SENIOR_DROP_HINTS.get(user_seniority, ())
    title_only = (lead.title or "").lower()
    for w in drop_words:
        # Match whole-word, not substring.
        if re.search(rf"\b{re.escape(w)}\b", title_only):
            return False, f"seniority_below_user:{w}"

    # 5. Budget — if hint suggests a tiny number AND user has a budget floor
    budget_floor = int(getattr(profile, "min_budget_usd", 0) or 0)
    if budget_floor > 0:
        hints = _budget_hints_usd(hay)
        if hints and max(hints) < budget_floor:
            return False, f"below_budget:{max(hints)}<floor:{budget_floor}"

    return True, ""


def filter_leads(leads: Iterable[Lead], profile: Any) -> list[Lead]:
    """Convenience: run each lead through the filter and return only keepers."""
    kept: list[Lead] = []
    for lead in leads:
        ok, reason = apply_profile_filter(lead, profile)
        if ok:
            kept.append(lead)
        else:
            log.info("profile_filter: drop %s — %s", lead.id, reason)
    return kept
