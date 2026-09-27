"""36 local evidence comparison tasks, citation consistency and search timings.

These checks measure traceability/lexical consistency, not expert relevance.
POSTs create saved local recommendation runs; no expansion calls are made.
"""
import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
CASES = {
    "科学通用底座": ["科学计算", "请帮我推荐能够开展科学计算的国内团队", "知识图谱", "科学计算与仿真", "科学计算，必须临床试验", "科学计算，排除科学计算"],
    "通用 AI": ["具身智能", "请帮我推荐能够开展具身智能的国内团队", "大模型", "机器人与具身智能", "具身智能，必须临床试验", "具身智能，排除具身智能"],
    "高能物理与量子科技": ["量子模拟", "请帮我推荐能够开展量子模拟的国内团队", "量子计算", "量子计算与模拟", "量子计算，必须临床试验", "量子计算，排除量子计算"],
    "化学与材料": ["催化", "请帮我推荐能够开展催化的国内团队", "能源材料", "催化与能源材料", "催化，必须临床试验", "催化，排除催化"],
    "生命科学与医学": ["肿瘤", "请帮我推荐能够开展肿瘤的国内团队", "蛋白质", "蛋白质与肿瘤", "蛋白质，必须临床试验", "肿瘤，排除肿瘤"],
    "地球科学": ["天气预报", "请帮我推荐能够开展天气预测的国内团队", "天气预测", "气候预测与海洋", "地震，必须临床试验", "海洋，排除海洋"],
}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", default="http://127.0.0.1:1607/v1/strategic-map")
    p.add_argument("--db", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    os.environ.update(STRATEGIC_MAP_DB_PATH=str(a.db.resolve()), STRATEGIC_MAP_SKIP_STARTUP_SYNC="true", STRATEGIC_MAP_SCHEDULER_ENABLED="false")
    from ai4s_tool.api import task_recommendations as tasks
    teams, claims, version = tasks._catalogue_evidence()
    cited = {(r[1], r[6], r[5]) for r in claims}
    domains = {t["domainName"]: t["domainId"] for t in teams}
    runs, durations, checks = [], [], []
    with httpx.Client(timeout=60, trust_env=False) as client:
        for name, queries in CASES.items():
            batch = []
            for index, query in enumerate(queries):
                start = time.perf_counter()
                response = client.post(a.base + "/task-recommendations", json={"taskText": query, "domainId": domains[name], "limit": 10})
                response.raise_for_status(); value = response.json()
                elapsed = (time.perf_counter() - start) * 1000
                citations = [(item["teamId"], c["url"], c["quote"]) for item in value["items"] for c in item["citations"]]
                correct = sum(c in cited for c in citations)
                checks.extend(c in cited for c in citations)
                assert correct == len(citations), (name, query, "unknown citation")
                if index == 5:
                    assert value["items"] == [], (name, "excluded topic returned")
                batch.append([(r["teamId"], r["taskMatchScore"]) for r in value["items"]])
                runs.append({"domain": name, "task": query, "runId": value["runId"], "understood": value["parsedTask"],
                    "count": len(value["items"]), "gap": value["shortfall"], "citations": len(citations), "consistentCitations": correct,
                    "milliseconds": round(elapsed, 2), "items": value["items"]})
            assert batch[0] == batch[1], (name, "politeness changes matching")
        query = {"q": "中国科学院", "page": 1, "size": 20, "verified_only": "true"}
        client.get(a.base + "/intelligence/search", params=query).raise_for_status()
        for _ in range(40):
            started = time.perf_counter()
            client.get(a.base + "/intelligence/search", params=query).raise_for_status()
            durations.append((time.perf_counter() - started) * 1000)
    output = {"version": version, "cases": runs, "metrics": {"cases": len(runs), "citationConsistency": sum(checks) / len(checks) if checks else None,
        "citationCount": len(checks), "shortLongPairsIdentical": 6, "emptyTasks": sum(r["count"] == 0 for r in runs),
        "searchP95Ms": round(sorted(durations)[37], 2), "searchMedianMs": round(statistics.median(durations), 2),
        "relevance": "未做专家盲审；程序检查不代替语义相关性评审", "paidCalls": 0}}
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output["metrics"], ensure_ascii=False))


if __name__ == "__main__":
    main()
