"""One explicit index contract plus a transparent, local fallback ordering."""
import logging
import re

logger = logging.getLogger(__name__)


def ranking_schema(count):
    size = min(3, count)
    return {"type": "object", "properties": {
        "indices": {"type": "array", "items": {"type": "integer", "enum": list(range(count))},
                    "minItems": size, "maxItems": size}},
        "required": ["indices"], "additionalProperties": False}


def ranking_failure(value, count):
    if not isinstance(value, dict):
        return "not_an_object"
    if "indices" not in value:
        return "missing_indices"
    if set(value) != {"indices"}:
        return "unexpected_fields"
    indices = value["indices"]
    if not isinstance(indices, list):
        return "indices_not_a_list"
    if not indices:
        return "empty_indices"
    if any(type(i) is not int for i in indices):
        return "non_integer_id"
    if any(i not in range(count) for i in indices):
        return "unknown_id"
    if len(set(indices)) != len(indices):
        return "duplicate_ids"
    if len(indices) != min(3, count):
        return "wrong_selection_count"
    return None


def log_ranking_failure(category, count, value=None):
    # Log only application-generated categories and integer IDs, never model text,
    # profile, article text, URLs, provider IDs, response objects or exception bodies.
    indices = value.get("indices") if isinstance(value, dict) else None
    safe_ids = [i for i in indices if type(i) is int and -1000000 <= i <= 1000000][:20] if isinstance(indices, list) else []
    logger.warning("Article ranking rejected category=%s candidate_count=%d "
                   "expected={indices: %d distinct integer IDs in 0..%d} received_integer_ids=%s",
                   category, count, min(3, count), count - 1, safe_ids)


def deterministic_indices(candidates, profile, gaps):
    """Order existing candidates by topic overlap, then publication date and index.

    This is a computed heuristic, never represented as an LLM relevance judgment.
    Call only with the already-verified, recent, deduplicated candidate list.
    """
    def tokens(text):
        return set(re.findall(r"[a-z0-9+#]+", text.lower()))

    interests = tokens(profile.get("interests", ""))
    weak = tokens(" ".join(gaps))

    def score(index):
        item = candidates[index]
        text = tokens(item.get("title", "") + " " + item.get("content", ""))
        overlap = len(text & interests) + 2 * len(text & weak)
        return (-overlap, -int(item["published"].replace("-", "")), index)

    return sorted(range(len(candidates)), key=score)[:3]
