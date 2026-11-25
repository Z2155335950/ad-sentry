"""平台政策 RAG：条款加载、BM25 检索与引用（PROMPT §8.7）。"""

import pytest
from app.tools import policy


def test_clause_corpus_has_at_least_twenty_unique_ids():
    clauses, by_id = policy.load_clauses()
    assert len(clauses) >= 20
    assert len(by_id) == len(clauses)
    assert {clause["platform"] for clause in clauses} == {"TikTok", "Meta"}


def test_ai_query_returns_ai_clause_with_date_and_url():
    results = policy.search("AI 标注义务")
    assert results
    top = results[0]
    assert "ai" in top["id"]
    assert top["effective_date"]
    assert top["url"].startswith("https://")
    assert "AI" in top["text"]


def test_english_query_matches():
    results = policy.search("AI-generated content disclosure")
    assert results and "ai" in results[0]["id"]


def test_platform_filter():
    results = policy.search("AI 标注义务", platform="Meta")
    assert results
    assert all(clause["platform"] == "Meta" for clause in results)


def test_unrelated_query_returns_empty():
    assert policy.search("zzzz qqqq unrelated phrase") == []


def test_policy_signal_is_p3_with_clause_evidence():
    signals = policy.policy_signals("AI 标注义务")
    assert len(signals) == 1
    signal = signals[0]
    assert signal["category"] == "policy"
    assert signal["level"] == "P3"
    evidence = signal["evidence"][0]
    assert evidence["kind"] == "clause"
    assert evidence["clause_id"] in policy.load_clauses()[1]
    assert evidence["ref_url"].startswith("https://")
    assert "生效" in evidence["note"]


def test_sale_text_does_not_match_policy():
    assert policy.search("SALE 50% OFF") == []


def test_top_k_limit():
    results = policy.search("AI 标注义务", top_k=1)
    assert len(results) <= 1


@pytest.mark.parametrize("query", ["", "   "])
def test_empty_query(query):
    assert policy.search(query) == []
