from __future__ import annotations

from agent.merchant_cleanup import parse_cleanup_suggestions, parse_cleanup_suggestions_with_stats


def test_parse_cleanup_suggestions_filters_and_clamps() -> None:
    payload = {
        "suggestions": [
            {
                "action": "rename",
                "source_merchant_id": "11111111-1111-1111-1111-111111111111",
                "target_merchant_id": None,
                "suggested_name": "  Coop City  ",
                "suggested_category": None,
                "confidence": 1.5,
                "rationale": "better display",
                "sample_aliases": ["COOP CITY", ""],
            },
            {
                "action": "merge",
                "source_merchant_id": "22222222-2222-2222-2222-222222222222",
                "target_merchant_id": None,
                "confidence": 0.9,
            },
            {
                "action": "categorize",
                "source_merchant_id": "33333333-3333-3333-3333-333333333333",
                "suggested_category": "Transport",
                "confidence": -5,
                "rationale": "desc",
                "sample_aliases": ["SBB"],
            },
        ]
    }

    suggestions = parse_cleanup_suggestions(payload)

    assert len(suggestions) == 2
    assert suggestions[0].action == "rename"
    assert suggestions[0].suggested_name == "Coop City"
    assert suggestions[0].confidence == 1.0
    assert suggestions[1].action == "categorize"
    assert suggestions[1].confidence == 0.0


def test_parse_cleanup_suggestions_with_stats_rejections() -> None:
    payload = {
        "suggestions": [
            {
                "action": "keep",
                "source_merchant_id": "11111111-1111-1111-1111-111111111111",
                "target_merchant_id": None,
                "confidence": 0.8,
            },
            {
                "action": "merge",
                "source_merchant_id": "22222222-2222-2222-2222-222222222222",
                "target_merchant_id": None,
                "confidence": 0.8,
            },
            {
                "action": "unknown",
                "source_merchant_id": "33333333-3333-3333-3333-333333333333",
                "confidence": 0.2,
            },
        ]
    }

    suggestions, stats = parse_cleanup_suggestions_with_stats(payload)

    assert len(suggestions) == 1
    assert stats["raw_count"] == 3
    assert stats["parsed_count"] == 1
    assert stats["rejected_count"] == 2
    assert stats["rejected_reasons"]["missing_ids"] == 1
    assert stats["rejected_reasons"]["invalid_action"] == 1
