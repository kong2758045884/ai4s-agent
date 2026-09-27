"""Collect and reconcile official life-science outcomes; no models or paid search.

Dry run is the default. An explicit --apply uses only the reviewed source-bound
records in --manifest. Every team gets a status, including no qualifying outcome.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def extract(team, page):
    """Conservative adapter for the IBP current group-head pages.

    Completed first-person group work only. Personal bibliographies, proposed
    work and institute-wide feeds are deliberately excluded.
    """
    url, text = page.get("url", ""), page.get("text", "")
    if not team["teamName"].endswith("研究组") or "/rc/" not in url or "ibp.cas.cn" not in url:
        return None, [], "需逐条核对成果归属；当前适配器不将目录或机构新闻作为本团队成果"
    name = team["teamName"][:-3]
    match = re.search(re.escape(name) + r"\s*/[^\n]{0,160}研究组长", text.replace("\n", " "))
    if not match:
        return None, [], "本页面缺少当前研究组长身份段落"
    identity = match.group()
    # compact() verifies this quote against the unmodified original body.
    ownership = {"teamId": team["id"], "quote": identity, "url": url, "sourceText": text,
                 "linkedUrls": [v["url"] for v in page.get("links", [])]}
    if "承担项目情况" not in text:
        return ownership, [], "页面未明确区分组内成果与个人履历，暂不发布"
    body = text.split("承担项目情况", 1)[1]
    body = re.split(r"代表论著|代表论文|近期论文|其他论著|参考文献", body)[0]
    quotes = []
    for sentence in re.split(r"(?<=[。！？])", body):
        sentence = sentence.strip()
        if len(sentence) > 1000:
            continue  # Do not flatten publication lists into one invented result.
        if (re.search(r"我们|本研究组|(?:本|我)?课题组|研究团队|本团队|本实验室", sentence)
                and re.search(r"发现了|揭示了|解析了|建立了|鉴定了|发展了|研发了|开发了|提出了|证明了|实现了|报道了|研制了|阐明了|取得了", sentence)
                and not re.search(r"将|希望|计划|致力于|力争|旨在|拟|担任|获得.{0,10}奖|基金", sentence)):
            quotes.append({"title": sentence[:100], "quote": sentence, "kind": "research_result", "publishedAt": ""})
    # Content dates remain unknown unless explicitly stated; crawl date is not publication date.
    return ownership, quotes[:4], "只有个人论文或研究方向，未发现明确归属的已完成团队成果" if not quotes else "官网当前研究组页面的组内成果陈述；原文校验，非人工专家评议"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args()
    if not args.db.is_file():
        parser.error("数据库不存在")
    os.environ.update(STRATEGIC_MAP_DB_PATH=str(args.db.resolve()), STRATEGIC_MAP_SKIP_STARTUP_SYNC="true", STRATEGIC_MAP_SCHEDULER_ENABLED="false")
    from ai4s_tool.api import task_recommendations as tasks, strategic_outcomes as outcomes
    teams = tasks._catalogue_evidence()[0]
    if args.apply or args.rollback:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        current = {t["id"]: t for t in teams}
        with sqlite3.connect(args.db) as conn:
            conn.row_factory = sqlite3.Row
            outcomes.init(conn)
            conn.execute("BEGIN IMMEDIATE")
            if args.rollback:
                result = {"revoked": outcomes.revoke_batch(conn, manifest["batchId"])}
            else:
                result = {"added": [], "duplicates": [], "conflicts": []}
                for item in manifest["items"]:
                    if not item["outcomes"]:
                        continue
                    team = current.get(item["teamId"])
                    if not team or (team["teamName"], team["institutionName"]) != (item["teamName"], item["institutionName"]):
                        result["conflicts"].append({"teamId": item["teamId"], "reason": "当前身份已变化或撤回"})
                        continue
                    added = outcomes.publish(conn, team, item["page"], item["outcomes"], batch_id=manifest["batchId"], ownership=item["ownership"])
                    result["added"].extend(added["added"])
                    result["duplicates"].extend(added["duplicates"])
        dest = args.manifest.with_suffix(".rollback.json" if args.rollback else ".applied.json")
        dest.write_text(json.dumps({"database": str(args.db.resolve()), "batchId": manifest["batchId"], **result}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({k: len(v) if isinstance(v, list) else v for k, v in result.items()}, ensure_ascii=False))
        return
    from ai4s_tool.api.official_team_directory import fetch_directory_page
    if args.cache is None:
        parser.error("采集需要 --cache")
    args.cache.mkdir(parents=True, exist_ok=True)
    def collect(team):
        url = team["sourceUrls"][0]
        file = args.cache / (team["id"] + "-0.json")
        page = json.loads(file.read_text(encoding="utf-8")) if file.exists() else fetch_directory_page(url) if args.fetch else {"url": url, "status": "cache_missing"}
        file.write_text(json.dumps(page, ensure_ascii=False), encoding="utf-8")
        ownership, found, reason = extract(team, page) if page.get("status") == "ok" else (None, [], "原文抓取失败：" + page.get("error", page["status"]))
        return {"teamId": team["id"], "teamName": team["teamName"], "institutionName": team["institutionName"],
                "status": "source_checked" if found else "no_qualified_outcome", "reason": reason,
                "page": page, "ownership": ownership, "outcomes": found}
    with ThreadPoolExecutor(max_workers=4) as pool:
        items = list(pool.map(collect, [t for t in teams if t["domainName"] == "生命科学与医学"]))
    manifest = {"batchId": "life-outcomes-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S"),
        "createdAt": datetime.now(timezone.utc).isoformat(), "mode": "dry-run", "items": items,
        "summary": {"units": len(items), "completed": sum(bool(i["outcomes"]) for i in items), "outcomes": sum(len(i["outcomes"]) for i in items)}}
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(manifest["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
