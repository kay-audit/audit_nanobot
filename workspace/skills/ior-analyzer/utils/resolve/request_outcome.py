"""Internal business outcomes; the public tool returns their text normally."""

class ClarificationRequired(ValueError):
    """A query cannot be executed safely until its business meaning is clear."""

    def __init__(self, message, *, stage=None, category=None, column=None):
        super().__init__(message)
        self.stage = stage
        self.category = category
        self.column = column
