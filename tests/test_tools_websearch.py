"""反向图搜：域名分类、Vision 响应归一化与定级规则（PROMPT §8.1）。"""

from app.tools import websearch


def test_classify_domain():
    assert websearch.classify_domain("https://www.shutterstock.com/image-photo/x") == "stock"
    assert websearch.classify_domain("https://stock.adobe.com/images/x") == "stock"
    assert websearch.classify_domain("https://www.amazon.com/dp/B000") == "ecommerce"
    assert websearch.classify_domain("https://www.instagram.com/p/abc") == "social"
    assert websearch.classify_domain("https://medium.com/@someone/post") == "blog"
    assert websearch.classify_domain("https://unknown.example.org/page") == "unknown"
    assert websearch.classify_domain("not a url") == "unknown"


def test_normalize_vision_response_flattens_all_sections():
    payload = {
        "responses": [
            {
                "webDetection": {
                    "pagesWithMatchingImages": [
                        {
                            "url": "https://www.gettyimages.com/detail/123",
                            "pageTitle": "Getty detail",
                            "fullMatchingImages": [{"url": "https://img.example/a.jpg"}],
                        }
                    ],
                    "fullMatchingImages": [{"url": "https://img.example/b.jpg"}],
                    "partialMatchingImages": [{"url": "https://img.example/c.jpg"}],
                    "visuallySimilarImages": [
                        {"url": "https://www.shutterstock.com/image/1"}
                    ],
                    "bestGuessLabels": [{"label": "poster"}],
                }
            }
        ]
    }
    normalized = websearch.normalize_vision_response(payload)
    assert normalized["best_guess_labels"] == ["poster"]
    by_url = {match["url"]: match for match in normalized["matches"]}
    assert by_url["https://www.gettyimages.com/detail/123"]["match_type"] == "full"
    assert by_url["https://www.gettyimages.com/detail/123"]["domain_kind"] == "stock"
    assert by_url["https://img.example/c.jpg"]["match_type"] == "partial"
    assert by_url["https://www.shutterstock.com/image/1"]["match_type"] == "similar"
    assert len(normalized["matches"]) == 4


def test_assess_stock_full_match_is_p0():
    normalized = {
        "matches": [
            {
                "url": "https://www.shutterstock.com/image-photo/1",
                "title": "Stock",
                "match_type": "full",
                "domain_kind": "stock",
            }
        ]
    }
    assessed = websearch.assess(normalized)
    assert assessed["level"] == "P0"
    assert assessed["confidence"] >= 0.7


def test_assess_unknown_full_match_is_p1():
    normalized = {
        "matches": [
            {
                "url": "https://unknown.example.org/a",
                "title": None,
                "match_type": "full",
                "domain_kind": "unknown",
            }
        ]
    }
    assert websearch.assess(normalized)["level"] == "P1"


def test_assess_similar_only_is_p2_and_empty_is_none():
    similar = {
        "matches": [
            {
                "url": "https://unknown.example.org/b",
                "title": None,
                "match_type": "similar",
                "domain_kind": "unknown",
            }
        ]
    }
    assert websearch.assess(similar)["level"] == "P2"
    assert websearch.assess({"matches": []})["level"] is None
