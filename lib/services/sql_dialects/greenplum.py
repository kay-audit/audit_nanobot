from __future__ import annotations

SQLGLOT_DIALECT = "postgres"


def generation_rules() -> str:
    return """Greenplum 6 / PostgreSQL-compatible static policy:
- Produce exactly one read-only SELECT/WITH statement.
- ILIKE, ||, :: casts, date_trunc and to_date are allowed.
- UNNEST and OFFSET are allowed where supported by Greenplum 6.
- Do not use Spark backtick identifiers or Spark-only functions.
- Validation is static-only: no live Greenplum execution/analyze is performed.
- Never invent tables or columns absent from the supplied KB cards."""

