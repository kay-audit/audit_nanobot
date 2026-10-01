from __future__ import annotations

SQLGLOT_DIALECT = "spark"


def generation_rules() -> str:
    return """Spark SQL policy:
- Produce exactly one read-only SELECT/WITH statement.
- Use date_add/date_sub/add_months/trunc and Spark datetime tokens yyyy, MM, dd, HH, mm, ss.
- Use concat() rather than || and CAST(x AS type) rather than x::type.
- Parse STRING dates with to_date(value, 'yyyy-MM-dd') when the KB type says STRING.
- Compare string flags/codes with quoted string literals.
- Use a subquery for row_number filtering; do not use QUALIFY.
- Never invent tables or columns absent from the supplied KB cards."""

