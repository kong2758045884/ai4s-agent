"""Public facts from reviewed runs AND deterministic official directories.

Directory membership proves a named research unit exists. It does not prove
staffing completeness, task fitness, or ownership of its parent's achievements.
This module is a read projection: it never changes verification or scores.
"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit

VERSION = "verified-directory-v3"


def citations(values):
    return [c for c in values or [] if isinstance(c, dict)
            and urlsplit(str(c.get("url") or "")).scheme in ("http", "https")
            and urlsplit(str(c.get("url") or "")).hostname
            and str(c.get("quote") or "").strip()]


def fact(value):
    return bool(isinstance(value, dict) and value.get("status") == "verified"
                and str(value.get("value") or "").strip() and citations(value.get("citations")))


def _identity(institution, team):
    # Same normalization as official_team_directory; do not merge parent/child.
    from .official_team_directory import _directory_key
    return _directory_key(institution, team)


def _official(url):
    host = (urlsplit(url).hostname or "").lower()
    return host.endswith((".edu.cn", ".cas.cn"))


def project(payload, observations):
    """Return (public team, cited facts) or None; observations are newest first."""
    if not payload.get("isDomestic") or payload.get("verificationStatus") in {
        "conflict", "duplicate", "out_of_scope", "rejected", "revoked",
    }:
        return None
    current_key = _identity(payload.get("institutionName", ""), payload.get("teamName", ""))
    seen = set()
    for entry in observations:
        status = entry["status"]
        if status in {"conflict", "revoked", "duplicate", "out_of_scope", "official_directory_conflict"}:
            return None
        if status not in {"verified", "official_directory"} or status in seen:
            continue
        seen.add(status)
        observation = entry["payload"]
        if not observation.get("published"):
            continue
        fields = []
        if status == "official_directory":
            from .official_team_directory import DIRECTORIES, _signature
            record = observation.get("record") or {}
            source = next((s for s in DIRECTORIES if s["institution"] == record.get("institution_name")
                           and s["url"] in record.get("source_urls", [])
                           and record.get("domain") in s["domains"]), None)
            if not source or observation.get("signature") != _signature(record):
                continue
            institution, team = record.get("institution_name", ""), record.get("team_name", "")
            basis = citations(record.get("citations"))
            def trusted(c):
                host = urlsplit(c["url"]).hostname
                return host == urlsplit(source["url"]).hostname or host in source.get("evidence_hosts", ())
            if not basis or not all(trusted(c) for c in basis):
                continue
            description = record.get("description", "")
            directions = record.get("directions", [])
            # Only explicitly parsed achievement sections become outcomes;
            # a general profile mentioning 'models' is not an achievement.
            outcome_index = {
                "official_named_group_card": 2,
                "official_named_lab_current_roster": 1,
                "official_research_department_with_current_head_and_results": 2,
            }.get(record.get("identity_basis"))
            fields = [("outcome" if i == outcome_index else "description", c["quote"], [c])
                      for i, c in enumerate(basis)]
            if record.get("identity_basis") == "official_research_unit_with_scoped_facts":
                fields = [("description", basis[0]["quote"], [basis[0]])]
                for value in record.get("facts", []):
                    refs = citations(value.get("citations"))
                    if value.get("kind") not in {"description", "direction", "outcome"} or not refs:
                        continue
                    if all(trusted(c) and c in basis for c in refs):
                        fields.append((value["kind"], value["text"], refs))
        else:
            reviewed = (observation.get("run") or {}).get("reviewed") or {}
            # Explicit withdrawal takes precedence over older observations.
            if reviewed.get("entity_relation") not in {"same", "rename"}:
                return None
            required = ("institution_name", "team_name", "concrete_team", "domestic", "domain_relevance")
            if any((reviewed.get(k) or {}).get("status") in {"rejected", "conflict"} for k in required):
                return None
            if not all(fact(reviewed.get(k)) for k in required):
                continue
            institution, team = reviewed["institution_name"]["value"], reviewed["team_name"]["value"]
            basis = citations(reviewed["institution_name"]["citations"])
            if not any(_official(c["url"]) for c in basis):
                continue
            description = next((reviewed[k]["value"] for k in ("advantage", "description")
                                if fact(reviewed.get(k))), "")
            directions = [f["value"] for f in reviewed.get("research_directions", []) if fact(f)]
            for key in ("team_name", "description", "advantage", "domain_relevance"):
                value = reviewed.get(key)
                if fact(value):
                    kind = "outcome" if key == "advantage" and re.search(
                        r"论文|成果|发表|项目|专利|模型|开源|系统|装置|平台|实验|Nature|Science|CVPR|ICLR",
                        value["value"], re.I) else "description"
                    fields.append((kind, value["value"], citations(value["citations"])))
            fields.extend(("direction", f["value"], citations(f["citations"]))
                          for f in reviewed.get("research_directions", []) if fact(f))
        if current_key != _identity(institution, team):
            continue
        claims = []
        for kind, text, refs in fields:
            for cite in refs:
                digest = hashlib.sha256(f"{payload['id']}\x1f{kind}\x1f{cite['url']}\x1f{cite['quote']}".encode()).hexdigest()[:32]
                claims.append((digest, payload["id"], entry["id"], kind, text, cite["quote"], cite["url"], ""))
        if not claims:
            continue
        public = {**payload, "institutionName": institution, "teamName": team,
                  "description": description, "researchDirections": directions,
                  "focus": "、".join(directions),
                  "coreDirection": payload.get("coreDirection", "") if payload.get("coreDirectionSource") == "manual" else "、".join(directions),
                  "sourceUrls": list(dict.fromkeys(c[6] for c in claims)),
                  "evidenceUrls": list(dict.fromkeys(c[6] for c in claims)),
                  "evidenceSummary": "\n".join(dict.fromkeys(c[5] for c in claims)),
                  "institutionEvidence": basis, "catalogueVersion": VERSION,
                  "catalogueSourceRunId": entry["id"], "catalogueBasis": status}
        return public, list({c[0]: c for c in claims}.values())
    return None
