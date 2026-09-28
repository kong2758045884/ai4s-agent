import hashlib
from datetime import datetime

from ai4s_tool.api import claim_provenance as source
from ai4s_tool.api import official_team_directory as directory
from tests.test_verified_team_catalogue import directory as observation, team
from ai4s_tool.api.verified_team_catalogue import project


def test_offsets_refer_to_full_saved_body_even_with_whitespace():
    text = "摘要。团队发表\n蛋白质  论文。附录"
    value = source.locate(text, "团队发表蛋白质论文")
    assert value["status"] == "whitespace_normalized"
    assert text[value["start"]:value["end"]] == "团队发表\n蛋白质  论文"
    assert source.locate(text, "没有出现的成果")["status"] == "not_found"
    assert source.locate("", "只有引文")["start"] is None
    excerpt = source.context(text, value, flank=3)
    assert excerpt == {"before": "摘要。", "matched": "团队发表\n蛋白质  论文", "after": "。附录"}
    assert excerpt["before"] + excerpt["matched"] + excerpt["after"] == text


def test_new_directory_citations_retain_actual_page_metadata_without_human_review():
    page = {"url": "https://example.edu.cn/lab", "title": "实验室成果",
            "text": "实验室成果。该团队发表蛋白质论文。",
            "fetched_at": "2026-09-25T10:00:00+08:00", "published_at": "2026-09-20"}
    cite = directory.citation(page, "该团队发表蛋白质论文")
    result = source.from_observation({"id": "r1", "status": "official_directory",
        "payload": {}, "created_at": datetime(2026, 9, 25)}, cite)
    assert result["sourceTitle"] == page["title"]
    assert result["fetchedAt"] == page["fetched_at"]
    assert result["publishedAt"] == "2026-09-20"
    assert result["contentHash"] == hashlib.sha256(page["text"].encode()).hexdigest()
    assert result["locator"]["basis"] == "extracted_text_at_collection"
    assert result["humanReview"]["status"] == "not_recorded"
    assert result["modelReview"]["status"] == "not_used"
    assert result["linkCheck"]["status"] == "not_checked"
    assert result["sourceContext"] is None  # Directory retained the offset, not the full body.


def test_legacy_quotes_do_not_fabricate_locations_or_dates():
    result = source.from_observation({"id": "old", "status": "official_directory", "payload": {}},
                                     {"url": "https://example.edu.cn", "quote": "历史片段"})
    assert result["locator"]["status"] == "body_not_retained"
    assert result["sourceContext"] is None
    assert result["contentHash"] == result["fetchedAt"] == result["sourceCheck"]["checkedAt"] == ""
    public, claims = project(team(), [observation()])
    assert all(public["claimProvenance"][claim[0]]["humanReview"]["status"] == "not_recorded" for claim in claims)


def test_ai_check_does_not_hide_quote_mismatch_or_claim_human_approval():
    cite = {"url": "https://example.edu.cn/lab", "quote": "实际没有的成果"}
    entry = {"id": "r2", "status": "verified", "created_at": "2026-09-26T12:00:00Z", "payload": {
        "run": {"contract_version": "v3", "pages": [{"url": cite["url"], "title": "研究简介", "text": "正在探索蛋白质研究"}]}}}
    value = source.from_observation(entry, cite)
    assert value["sourceCheck"]["status"] == "quote_mismatch"
    assert value["modelReview"]["status"] == "reviewed"
    assert value["humanReview"]["status"] == "not_recorded"


def test_outcome_check_carries_ownership_and_does_not_relabel_claim_as_page_title():
    row = {"url": "https://example.edu.cn/result", "quote": "团队甲发表成果", "source_text": "2026年团队甲发表成果。",
           "team_id": "t1", "title": "提炼后的主张", "batch_id": "batch1", "published_at": "2026-09-01",
           "fetched_at": "2026-09-25", "created_at": "2026-09-26", "review_method": "official-outcome-rule"}
    value = source.from_outcome(row)
    assert value["sourceTitle"] == ""
    assert value["ownershipCheck"]["teamId"] == "t1"
    assert value["locator"]["status"] == "exact"
    assert value["sourceContext"] == {"before": "2026年", "matched": "团队甲发表成果", "after": "。"}
    assert value["humanReview"]["status"] == "not_recorded"


def test_directory_provenance_rejects_quote_missing_from_captured_page():
    entry = observation()
    record = entry["payload"]["record"]
    for cite in record["citations"]:
        cite["quote_locator"] = source.locate("不同的原文", cite["quote"])
    entry["payload"]["signature"] = directory._signature(record)
    assert project(team(), [entry]) is None
