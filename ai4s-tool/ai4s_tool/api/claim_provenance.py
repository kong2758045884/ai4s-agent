"""Describe recorded source checks without inferring human review or availability."""
from __future__ import annotations

import hashlib
from datetime import datetime
from urllib.parse import urlsplit

VERSION = "claim-provenance-v2"


def stamp(value):
    return value.isoformat() if isinstance(value, datetime) else str(value or "")


def locate(text, quote):
    """Offsets refer to saved extracted text, not HTML or an unretained webpage."""
    if not text or not quote:
        return {"status": "body_not_retained", "start": None, "end": None, "basis": "saved_extracted_text"}
    start = text.find(quote)
    if start >= 0:
        return {"status": "exact", "start": start, "end": start + len(quote), "basis": "saved_extracted_text"}
    positions = [i for i, char in enumerate(text) if not char.isspace()]
    compact_text = "".join(text[i] for i in positions)
    compact_quote = "".join(c for c in quote if not c.isspace())
    start = compact_text.find(compact_quote) if compact_quote else -1
    if start < 0:
        return {"status": "not_found", "start": None, "end": None, "basis": "saved_extracted_text"}
    return {"status": "whitespace_normalized", "start": positions[start],
            "end": positions[start + len(compact_quote) - 1] + 1, "basis": "saved_extracted_text"}


def context(text, locator, flank=120):
    """Freeze a bounded passage from the same extracted body as the offsets."""
    if locator["status"] not in {"exact", "whitespace_normalized"}:
        return None
    start, end = locator["start"], locator["end"]
    if start is None or end is None or not 0 <= start < end <= len(text):
        return None
    return {"before": text[max(0, start - flank):start], "matched": text[start:end],
            "after": text[end:end + flank]}


def _base(url, quote, *, run_id, title="", published_at="", fetched_at="", checked_at="", text="", method="", source_type=""):
    official = (urlsplit(url).hostname or "").endswith((".edu.cn", ".cas.cn"))
    locator = locate(text, quote)
    return {"version": VERSION, "sourceRunId": run_id, "sourceTitle": title,
        "sourceType": source_type or ("official_institution" if official else "publication_or_webpage"),
        "publishedAt": stamp(published_at), "fetchedAt": stamp(fetched_at),
        "contentHash": hashlib.sha256(text.encode()).hexdigest() if text else "",
        "quoteHash": hashlib.sha256(quote.encode()).hexdigest(), "locator": locator,
        "sourceContext": context(text, locator),
        "sourceCheck": {"method": method, "checkedAt": stamp(checked_at), "status": "recorded"},
        "modelReview": {"status": "not_used", "version": "", "reviewedAt": ""},
        "humanReview": {"status": "not_recorded", "reviewedAt": "", "reviewer": ""},
        "linkCheck": {"status": "not_checked", "checkedAt": ""}}


def from_observation(entry, cite):
    payload = entry["payload"]
    run = payload.get("run") or {}
    page = next((p for p in run.get("pages", []) if p.get("url") == cite["url"]), {})
    directory = entry["status"] == "official_directory"
    result = _base(cite["url"], cite["quote"], run_id=entry["id"],
        title=page.get("title") or cite.get("title", ""), text=page.get("text", ""),
        published_at=page.get("published_at") or cite.get("published_at", ""),
        fetched_at=page.get("fetched_at") or cite.get("fetched_at", ""), checked_at=entry.get("created_at"),
        method=payload.get("rule") or ("official-directory-citation-rule" if directory else "independent-model-and-quote-validation"),
        source_type=cite.get("source_type", ""))
    if directory:
        # Old directory entries kept quotes but not full bodies. Do not fabricate
        # offsets or use the quote itself as proof that it occurred in a body.
        result["contentHash"] = cite.get("content_hash", "")
        if cite.get("quote_locator"):
            result["locator"] = {**cite["quote_locator"], "basis": "extracted_text_at_collection"}
    else:
        result["modelReview"] = {"status": "reviewed", "version": str(run.get("contract_version") or run.get("version") or ""),
                                  "reviewedAt": stamp(entry.get("created_at"))}
    if result["locator"]["status"] == "not_found":
        result["sourceCheck"]["status"] = "quote_mismatch"
    return result


def from_outcome(row):
    result = _base(row["url"], row["quote"], run_id=row["batch_id"],
        published_at=row["published_at"], fetched_at=row["fetched_at"], checked_at=row["created_at"],
        text=row["source_text"], method=row["review_method"])
    result["ownershipCheck"] = {"status": "recorded", "teamId": row["team_id"], "method": row["review_method"]}
    return result
