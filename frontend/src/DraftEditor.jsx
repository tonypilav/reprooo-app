import { useRef, useState } from "react";
import {
  Select,
  Alert,
  Badge,
  Button,
  Group,
  Paper,
  Stack,
  Text,
  Textarea,
  Title,
} from "@mantine/core";
import { createTicket, isSessionUnavailable, clarificationOperation, getRefinement } from "./api";
import { mergeCheckpoint, openQuestionsPreview } from "./refinementState";
import { t } from "./texts";

function DraftEditor({ template, initialDraft, initialRevision = 1, sessionId, onReset, onSessionUnavailable }) {
  const [draft, setDraft] = useState(initialDraft);
  const [ticket, setTicket] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const [checkpoint, setCheckpoint] = useState(null);
  const [revision, setRevision] = useState(initialRevision);
  const [answers, setAnswers] = useState({});
  const [review, setReview] = useState(null);
  const [recovery, setRecovery] = useState(null);
  const [conflict, setConflict] = useState(false);
  const [submissionPreview, setSubmissionPreview] = useState(null);
  const local = useRef(initialDraft);
  const answerVersion = useRef(0);
  const retry = useRef(null);
  const reviewed = useRef(null);
  const c = t.clarification;
  const disabled = loading || !sessionId || conflict;
  const label = (key) => template.fields.find((field) => field.key === key)?.label || key;

  function replaceDraft(value) { local.current = value; setDraft(value); }
  function updateAnswer(q, patch) {
    answerVersion.current += 1;
    setAnswers((current) => ({ ...current, [q.id]: {
      question_id: q.id, status: q.status, answer: q.answer, ...current[q.id], ...patch,
    } }));
  }
  function handleFailure(error) {
    if (isSessionUnavailable(error)) onSessionUnavailable();
    else if (error.code === 'REVISION_CONFLICT') { setConflict(true); setError(c.conflictError); }
    else setError(c.failure);
  }
  async function operate(kind, extra = {}) {
    if (!sessionId || conflict || loading) return;
    const submitted = { ...local.current };
    const version = answerVersion.current;
    const body = { session_id: sessionId, template: { mode: template.mode, version: template.version },
      expected_revision: revision, draft: submitted, review_token: reviewed.current, ...extra };
    const signature = JSON.stringify({ kind, body });
    if (retry.current?.signature !== signature) retry.current = { signature, request_id: crypto.randomUUID() };
    setLoading(true); setError(''); setReview(null);
    try {
      const result = await clarificationOperation(kind, { ...body, request_id: retry.current.request_id });
      if (result.status === 'preview_review_required') { setReview({ ...result, submitted, version }); return; }
      retry.current = null; reviewed.current = null;
      setCheckpoint(result); setRevision(result.revision);
      replaceDraft(mergeCheckpoint(local.current, submitted, result.draft));
      const questions = new Map((result.question_history || result.questions).map((q) => [q.id, q]));
      const acknowledged = new Map((kind === 'refine' ? body.answers : []).map((a) => [a.question_id, a]));
      setAnswers((pending) => Object.fromEntries(Object.entries(pending).filter(([id, answer]) => {
        const question = questions.get(id);
        if (!question || question.superseded_by) return false;
        return acknowledged.get(id) !== answer
          || question.status !== answer.status || question.answer !== answer.answer;
      })));
    } catch (error) { handleFailure(error); }
    finally { setLoading(false); }
  }
  async function recover() {
    if (!sessionId || loading) return;
    setLoading(true);
    try { setRecovery(await getRefinement(sessionId)); }
    catch (error) { handleFailure(error); }
    finally { setLoading(false); }
  }

  const activeIds = new Set(checkpoint?.questions.map((q) => q.id) || []);
  const visibleQuestions = [...(checkpoint?.questions || []),
    ...(checkpoint?.question_history || []).filter((q) => !activeIds.has(q.id))];

  const missing = template.fields
    .filter((field) => field.required && !(draft[field.key] || "").trim())
    .map((field) => field.key);

  function handleChange(key, value) {
    replaceDraft({ ...local.current, [key]: value });
    setSubmissionPreview(null);
  }

  async function handleCreate(includeQuestions) {
    if (!sessionId || loading || conflict) return;
    if (typeof includeQuestions !== 'boolean' && checkpoint?.open_question_items?.length) {
      setSubmissionPreview(openQuestionsPreview(local.current.open_questions, checkpoint.open_question_items));
      return;
    }
    const submitted = { ...local.current };
    if (includeQuestions) submitted.open_questions = openQuestionsPreview(submitted.open_questions, checkpoint.open_question_items);
    setLoading(true); setError('');
    try {
      const result = await createTicket(sessionId, submitted, template,
        { expected_revision: revision, request_id: crypto.randomUUID() });
      if (result.status === 'preview_review_required') {
        setReview({ ...result, submitted, version: answerVersion.current });
        setSubmissionPreview(null);
      } else setTicket(result);
    } catch (error) { handleFailure(error); }
    finally { setLoading(false); }
  }

  if (ticket) {
    return (
      <Stack>
        <Group>
          <Title order={3}>{t.editor.created}</Title>
          <Badge size="lg">{ticket.key}</Badge>
        </Group>
        <Text fw={500}>{ticket.summary}</Text>
        <Paper bg="dark.6" withBorder={false} p="md">
          <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
            {ticket.description}
          </Text>
        </Paper>
        <Group justify="flex-end">
          <Button onClick={onReset}>{t.editor.reset}</Button>
        </Group>
      </Stack>
    );
  }

  return (
    <Stack>
      <Title order={3}>{t.editor.title}</Title>

      {error && (
        <Alert color="red" variant="light">
          {error}
        </Alert>
      )}

      {template.fields.map((field) => (
        <Textarea
          key={field.key}
          label={field.label}
          description={field.hint}
          withAsterisk={field.required}
          value={draft[field.key] || ""}
          onChange={(event) =>
            handleChange(field.key, event.currentTarget.value)
          }
          error={missing.includes(field.key) ? t.editor.fillField : undefined}
          autosize
          minRows={field.key === "summary" ? 1 : 3}
        />
      ))}

      <Group>
        <Button disabled={disabled} onClick={() => operate('analyze')}>{checkpoint ? c.again : c.analyze}</Button>
        <Button variant="default" disabled={loading || !sessionId} onClick={recover}>{c.recover}</Button>
      </Group>
      {review && <Paper withBorder p="sm"><Stack>
        <Text>{c.review}</Text>
        {Object.entries(review.draft).map(([key, value]) => <Text key={key} style={{ whiteSpace: 'pre-wrap' }}>{label(key)}: {value}</Text>)}
        {review.answers?.map((a) => <Text key={a.question_id}>{a.answer}</Text>)}
        {review.history?.map((q) => <Text key={q.id}>{q.text} · {q.answer} · {q.rationale} · {q.evidence}</Text>)}
        {review.proposals?.map((p) => <Text key={p.id}>{p.replacement} · {p.reason}</Text>)}
        <Button disabled={loading} onClick={() => {
          replaceDraft(mergeCheckpoint(local.current, review.submitted, review.draft));
          if (answerVersion.current === review.version && review.answers?.length) {
            setAnswers(Object.fromEntries(review.answers.map((a) => [a.question_id, a])));
          }
          reviewed.current = review.review_token || null;
          retry.current = null; setReview(null); setError('');
        }}>{c.applyReview}</Button>
      </Stack></Paper>}
      {recovery && <Paper withBorder p="sm"><Stack>
        {Object.entries(recovery.draft).map(([key, value]) => <Text key={key} style={{ whiteSpace: 'pre-wrap' }}>{label(key)}: {value}</Text>)}
        <Button disabled={loading} onClick={() => {
          replaceDraft(recovery.draft); setCheckpoint(recovery); setRevision(recovery.revision);
          setAnswers({}); reviewed.current = null; setConflict(false); setRecovery(null); setReview(null); retry.current = null; setError('');
        }}>{c.adopt}</Button>
      </Stack></Paper>}
      {checkpoint?.questions.length === 0 && <Text>{c.noQuestions}</Text>}
      {visibleQuestions.map((q) => {
        const value = answers[q.id] || q;
        return <Paper key={q.id} withBorder p="sm"><Stack>
          {!activeIds.has(q.id) && <Text size="sm" c="dimmed">{q.superseded_by ? c.superseded : c.savedQuestion}</Text>}
          <Text fw={600}>{q.text}</Text>
          <Text size="sm">{c.priorities[q.priority]} · {q.section_ids.map(label).join(', ')}</Text>
          <Text size="sm">{q.rationale}</Text>
          {q.evidence && <Text size="sm">{q.evidence}</Text>}
          <Select label={c.disposition} value={value.status} disabled={loading || !!q.superseded_by}
            data={Object.entries(c.statuses).map(([value, label]) => ({ value, label }))}
            onChange={(status) => { if (status) updateAnswer(q, { status, answer: status === 'answered' ? value.answer : '' }); }} />
          <Textarea label={c.answer} value={value.answer} disabled={loading || !!q.superseded_by}
            onChange={(event) => updateAnswer(q, { answer: event.currentTarget.value, status: 'answered' })} />
        </Stack></Paper>;
      })}
      {checkpoint && <Button disabled={disabled} onClick={() => operate('refine', { answers: Object.values(answers) })}>{c.refine}</Button>}
      {checkpoint?.protected_sections.map((section) => <Group key={section}>
        <Text>{label(section)}: {c.protected}</Text>
        <Button disabled={disabled} variant="default" onClick={() => operate('decisions', { allow_sections: [section] })}>{c.allow}</Button>
      </Group>)}
      {checkpoint?.proposals.map((p) => <Paper key={p.id} withBorder p="sm"><Stack>
        <Text fw={600}>{label(p.section_id)}</Text>
        <Text>{c.current}</Text><Text style={{ whiteSpace: 'pre-wrap' }}>{draft[p.section_id]}</Text>
        <Text>{c.proposed}</Text><Text style={{ whiteSpace: 'pre-wrap' }}>{p.replacement}</Text>
        <Text>{p.reason}</Text>
        {p.question_ids.map((id) => <Text key={id} size="sm">{(checkpoint.question_history || checkpoint.questions).find((q) => q.id === id)?.answer || id}</Text>)}
        {p.conflict && <Alert color="yellow">{c.conflict}</Alert>}
        <Group>{['accept', 'reject'].map((action) => <Button key={action}
          disabled={disabled || draft[p.section_id] !== p.base_text}
          onClick={() => operate('decisions', { batch_id: checkpoint.batch_id, decisions: [{ proposal_id: p.id, action }] })}>{c[action]}</Button>)}</Group>
      </Stack></Paper>)}
      {submissionPreview !== null && <Paper withBorder p="sm"><Stack>
        <Text>{c.preview}</Text><Text style={{ whiteSpace: 'pre-wrap' }}>{submissionPreview}</Text>
        <Button disabled={disabled} onClick={() => handleCreate(true)}>{c.include}</Button>
        <Button disabled={disabled} variant="default" onClick={() => handleCreate(false)}>{c.decline}</Button>
        <Button variant="subtle" onClick={() => setSubmissionPreview(null)}>{c.cancel}</Button>
      </Stack></Paper>}

      <Group justify="space-between">
        <Button variant="default" onClick={onReset} disabled={loading}>
          {t.editor.reset}
        </Button>
        <Button
          onClick={handleCreate}
          loading={loading}
          disabled={disabled || missing.length > 0 || review !== null}
        >
          {t.editor.create}
        </Button>
      </Group>
    </Stack>
  );
}

export default DraftEditor;
