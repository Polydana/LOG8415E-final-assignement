
import re

READ_RE = re.compile(r"^\s*select\b", re.I)
WRITE_RE = re.compile(r"^\s*(insert|update|delete)\b", re.I)


def validate_sql(query: str):
    """
    Very simple SQL validator:
    - No empty queries
    - No multiple statements
    - Only allow SELECT for reads and INSERT/UPDATE/DELETE for writes
    """
    if not query or not query.strip():
        return False, "empty query not allowed"

    q = query.strip()

    # Disallow multiple statements (simple check)
    if ";" in q[:-1]:
        return False, "multiple statements not allowed"

    if READ_RE.match(q):
        return True, "ok"

    if WRITE_RE.match(q):
        return True, "ok"

    return False, "only SELECT/INSERT/UPDATE/DELETE statements allowed"
