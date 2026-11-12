"""Demo resource bounds shared by request and generated-content validation."""
BODY_BYTES = 262144
CONTEXT_CHARS = 20000
PREVIEW_CHARS = 40000
FIELD_CHARS = 20000
DRAFT_CHARS = 50000
ANSWER_CHARS = 4000
ANSWERS_CHARS = 20000
ANSWERS_COUNT = 20
REVIEW_TOKEN_CHARS = 196608
HISTORY_CHARS = 40000


class CapacityError(Exception):
    """Server storage exhausted; no submitted values in the error."""


class RefinementLimitError(Exception):
    """The operation cannot fit without losing retained user state."""


def valid_draft_size(draft):
    return (len(draft) <= 5 and all(len(value) <= FIELD_CHARS for value in draft.values())
            and sum(map(len, draft.values())) <= DRAFT_CHARS)
