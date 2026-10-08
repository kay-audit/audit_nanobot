"""Internal business outcomes; the public tool returns their text normally."""

class ClarificationRequired(ValueError):
    """A query cannot be executed safely until its business meaning is clear."""
