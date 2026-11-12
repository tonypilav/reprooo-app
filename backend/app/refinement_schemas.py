"""Bounded requirements-v1 clarification contracts."""
from typing import Annotated, Literal
from copy import deepcopy
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, AfterValidator, model_validator
from app.schemas import SessionId, TemplateReference, validate_draft_size
from app.limits import ANSWER_CHARS, ANSWERS_CHARS, ANSWERS_COUNT, REVIEW_TOKEN_CHARS

Section = Literal['summary', 'business_context', 'requirements', 'acceptance_criteria', 'open_questions']
Status = Literal['unanswered', 'answered', 'skipped', 'not_applicable', 'left_to_implementation', 'needs_stakeholder_decision']
Text = Annotated[str, StringConstraints(strict=True, max_length=20000)]
AnswerText = Annotated[str, StringConstraints(strict=True, max_length=ANSWER_CHARS)]
RefinementDraft = Annotated[dict[Section, Text], Field(max_length=5), AfterValidator(validate_draft_size)]
Id = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')]

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

class Question(StrictModel):
    id: Id
    text: Annotated[Text, Field(min_length=1, max_length=1000)]
    section_ids: Annotated[list[Section], Field(min_length=1, max_length=5)]
    priority: Literal['blocking', 'recommended', 'optional']
    rationale: Annotated[Text, Field(max_length=1000)]
    evidence: Annotated[Text, Field(max_length=2000)]

class QuestionState(Question):
    status: Status = 'unanswered'
    answer: AnswerText = ''
    superseded_by: Id | None = None

class QuestionBatch(StrictModel):
    questions: Annotated[list[Question], Field(max_length=5)]

class ProposedChange(StrictModel):
    section_id: Section
    replacement: Text
    reason: Annotated[Text, Field(max_length=1000)]
    question_ids: Annotated[list[Id], Field(min_length=1, max_length=5)]
    conflict: bool

class ProposalBatch(StrictModel):
    proposals: Annotated[list[ProposedChange], Field(max_length=5)]

class Proposal(ProposedChange):
    id: Id
    base_text: Text

class Answer(StrictModel):
    question_id: Id
    status: Status
    answer: AnswerText = ''

class Mutation(StrictModel):
    session_id: SessionId
    template: TemplateReference
    expected_revision: Annotated[int, Field(ge=1)]
    request_id: Id
    draft: RefinementDraft
    review_token: Annotated[str, StringConstraints(strict=True, max_length=REVIEW_TOKEN_CHARS)] | None = None

class AnalyzeRequest(Mutation):
    pass

class RefineRequest(Mutation):
    answers: Annotated[list[Answer], Field(max_length=ANSWERS_COUNT)] = []

    @model_validator(mode='after')
    def bounded_answers(self):
        if sum(len(answer.answer) for answer in self.answers) > ANSWERS_CHARS:
            raise ValueError('Invalid answers size')
        return self

class Decision(StrictModel):
    proposal_id: Id
    action: Literal['accept', 'reject']

class DecisionsRequest(Mutation):
    batch_id: Id | None = None
    decisions: Annotated[list[Decision], Field(max_length=5)] = []
    allow_sections: Annotated[list[Section], Field(max_length=5)] = []

class Checkpoint(StrictModel):
    template: TemplateReference
    draft: RefinementDraft
    revision: int
    missing_fields: list[Section]
    questions: list[QuestionState]
    question_history: list[QuestionState]
    proposals: list[Proposal]
    batch_id: Id | None
    base_revision: int | None
    protected_sections: list[Section]
    open_question_items: list[Text]
    open_questions_preview: str
    recent_request_ids: list[Id]

class RefinementReview(StrictModel):
    status: Literal['preview_review_required'] = 'preview_review_required'
    draft: RefinementDraft
    answers: list[Answer] = []
    history: list[QuestionState] = []
    proposals: list[Proposal] = []
    review_token: Annotated[str, StringConstraints(strict=True, max_length=REVIEW_TOKEN_CHARS)] | None = None


def sanitize_refinement_content(masker, context, value):
    """Validate known payload shapes; mask free text together, never metadata.

    Keep this list explicit: new payload fields must declare their content boundary.
    Request/output envelopes are additionally checked by their existing schemas.
    """
    result = deepcopy(value)
    leaves = {}

    def content(node, fields, path):
        for field in fields:
            if field in node:
                leaves[path + (field,)] = node[field]

    for key, node in result.items():
        if key == 'draft':
            TypeAdapter(RefinementDraft).validate_python(node, strict=True)
            content(node, node, (key,))
        elif key in ('eligible_sections', 'protected_sections'):
            TypeAdapter(list[Section]).validate_python(node, strict=True)
        elif key in ('answers', 'history', 'questions', 'proposals'):
            for index, item in enumerate(node):
                if key == 'answers':
                    schema, fields = Answer, ('answer',)
                elif key in ('history', 'questions'):
                    schema = QuestionState if key == 'history' or 'status' in item else Question
                    fields = ('text', 'rationale', 'evidence', 'answer')
                else:
                    schema = Proposal if 'id' in item or 'base_text' in item else ProposedChange
                    fields = ('replacement', 'reason', 'base_text')
                schema.model_validate(item)
                content(item, fields, (key, index))
        else:
            raise ValueError('Unknown refinement payload field')

    safe = masker.sanitize_fields(leaves, context)
    for path, text in safe.items():
        node = result
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = text
    return result
