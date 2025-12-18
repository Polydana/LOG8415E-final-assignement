from typing import Tuple


DANGEROUS_KEYWORDS = [
    "drop ",
    "truncate ",
    "alter ",
    "shutdown",
    "grant ",
    "revoke ",
]


def validate_sql(query: str) -> Tuple[bool, str]:
    """
    Returns (is_valid, reason_if_not_valid).
    This is intentionally simple but enough for the assignment.
    """

    q = query.strip()
    q_lower = q.lower()

    if not q:
        return False, "Empty query"

    # Disallow very dangerous keywords
    for kw in DANGEROUS_KEYWORDS:
        if kw in q_lower:
            return False, f"Query contains forbidden keyword: {kw.strip()}"

    # For UPDATE and DELETE, require a WHERE clause
    if q_lower.startswith("delete") or q_lower.startswith("update"):
        if " where " not in q_lower:
            return False, "UPDATE/DELETE without WHERE is not allowed"

    # Optionally limit query length to avoid abuse
    if len(q) > 5000:
        return False, "Query too long"

    return True, ""
