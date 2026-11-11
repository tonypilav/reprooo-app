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
import { requiredFinalFields, finalSnapshot, serializeFinal, copyFinal, downloadFinal, finalNotification, notificationColors } from "./finalization";
import "./finalization.css";
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
  const [prepared, setPrepared] = useState(null);
  const [inclusion, setInclusion] = useState(null);
  const [notice, setNotice] = useState(null);
  const [everPrepared, setEverPrepared] = useState(false);
  const editVersion = useRef(0);
  const busy = useRef(false);
  const finalRef = useRef(null);
  const includedText = useRef(null);
  const [newerSubmissionEdits, setNewerSubmissionEdits] = useState(false);
  const local = useRef(initialDraft);
  const answerVersion = useRef(0);
  const retry = useRef(null);
  const reviewed = useRef(null);
  const c = t.clarification;
  const f = t.finalization;
  const disabled = loading || !sessionId || conflict || !!ticket;
  const label = (key) => template.fields.find((field) => field.key === key)?.label || key;

  function invalidate() {
    editVersion.current += 1;
    finalRef.current = null; setPrepared(null); setNotice('');
  }
  function replaceDraft(value) { local.current = value; setDraft(value); }
  function updateAnswer(q, patch) {
    invalidate(); setInclusion(null); setSubmissionPreview(null);
    answerVersion.current += 1;
    setAnswers((current) => ({ ...current, [q.id]: {
      question_id: q.id, status: q.status, answer: q.answer, ...current[q.id], ...patch,
    } }));
  }
  function handleFailure(error) {
    invalidate();
    if (isSessionUnavailable(error)) onSessionUnavailable();
    else if (error.code === 'REVISION_CONFLICT') { setConflict(true); setError(c.conflictError); }
    else setError(c.failure);
  }
  async function operate(kind, extra = {}, prepare = false, candidate = local.current, choice = inclusion) {
    if (!sessionId || conflict || busy.current || ticket) return;
    invalidate();
    const generation = editVersion.current;
    busy.current = true;
    const baseline = { ...local.current };
    const submitted = { ...candidate };
    const version = answerVersion.current;
    const body = { session_id: sessionId, template: { mode: template.mode, version: template.version },
      expected_revision: revision, draft: submitted, review_token: reviewed.current, ...extra };
    const signature = JSON.stringify({ kind, body });
    if (retry.current?.signature !== signature) retry.current = { signature, request_id: crypto.randomUUID() };
    setLoading(true); setError(''); setReview(null);
    try {
      const result = await clarificationOperation(kind, { ...body, request_id: retry.current.request_id });
      if (result.status === 'preview_review_required') { setReview({ ...result, submitted: baseline, version }); return; }
      retry.current = null; reviewed.current = null;
      setCheckpoint(result); setRevision(result.revision);
      replaceDraft(mergeCheckpoint(local.current, baseline, result.draft));
      if (!prepare) setInclusion(null);
      if (prepare && editVersion.current === generation) {
        const needsChoice = result.open_question_items?.length > 0 && choice === null;
        if (needsChoice) setSubmissionPreview(openQuestionsPreview(result.draft.open_questions, result.open_question_items));
        const snapshot = needsChoice ? null : finalSnapshot(result, choice);
        if (snapshot) {
          if (choice === true) {
            const before = includedText.current?.after === baseline.open_questions
              ? includedText.current.before : baseline.open_questions;
            includedText.current = { before, after: result.draft.open_questions };
          }
          setEverPrepared(true);
          finalRef.current = snapshot; setPrepared(snapshot);
        }
        else if (!needsChoice) setError(f.missing);
      }
      const questions = new Map((result.question_history || result.questions).map((q) => [q.id, q]));
      const acknowledged = new Map((kind === 'refine' ? body.answers : []).map((a) => [a.question_id, a]));
      setAnswers((pending) => Object.fromEntries(Object.entries(pending).filter(([id, answer]) => {
        const question = questions.get(id);
        if (!question || question.superseded_by) return false;
        return acknowledged.get(id) !== answer
          || question.status !== answer.status || question.answer !== answer.answer;
      })));
    } catch (error) { handleFailure(error); }
    finally { busy.current = false; setLoading(false); }
  }
  async function recover() {
    if (!sessionId || busy.current || ticket) return;
    invalidate(); busy.current = true;
    setLoading(true);
    try { setRecovery(await getRefinement(sessionId)); }
    catch (error) { handleFailure(error); }
    finally { busy.current = false; setLoading(false); }
  }

  const activeIds = new Set(checkpoint?.questions.map((q) => q.id) || []);
  const visibleQuestions = [...(checkpoint?.questions || []),
    ...(checkpoint?.question_history || []).filter((q) => !activeIds.has(q.id))];

  const missing = requiredFinalFields(draft);

  function handleChange(key, value) {
    invalidate();
    replaceDraft({ ...local.current, [key]: value });
    setSubmissionPreview(null);
  }

  async function handlePrepare(choice = inclusion) {
    if (disabled || review || Object.keys(answers).length || requiredFinalFields(local.current).length) return;
    if (checkpoint?.open_question_items?.length && choice === null) {
      setSubmissionPreview(openQuestionsPreview(local.current.open_questions, checkpoint.open_question_items));
      return;
    }
    const candidate = { ...local.current };
    if (choice === true) candidate.open_questions = openQuestionsPreview(candidate.open_questions, checkpoint.open_question_items);
    await operate('decisions', { decisions: [] }, true, candidate, choice);
  }

  async function handleCreate() {
    const snapshot = finalRef.current;
    if (!snapshot || disabled || busy.current || review) return;
    const generation = editVersion.current;
    busy.current = true;
    setLoading(true); setError('');
    try {
      const result = await createTicket(sessionId, snapshot.draft, template,
        { expected_revision: snapshot.revision, request_id: crypto.randomUUID() });
      if (result.status === 'preview_review_required') {
        invalidate();
        setReview({ ...result, submitted: snapshot.draft, version: answerVersion.current });
      } else {
        setNewerSubmissionEdits(editVersion.current !== generation);
        setTicket(result);
        finalRef.current = null; setPrepared(null);
      }
    } catch (error) { handleFailure(error); }
    finally { busy.current = false; setLoading(false); }
  }

  async function handleCopy(format) {
    if (!finalRef.current || disabled) return;
    try { await copyFinal(finalRef.current, format); setNotice(finalNotification('copied')); }
    catch { setNotice(finalNotification('copyFailed')); }
  }

  function handleDownload() {
    if (!finalRef.current || disabled) return;
    try { downloadFinal(finalRef.current); }
    catch { setNotice(finalNotification('downloadFailed')); }
  }

  if (ticket && !newerSubmissionEdits) {
    return (
      <Stack>
        <Group>
          <Title order={3}>{t.editor.created}</Title>
          <Badge size="lg">{ticket.key}</Badge>
        </Group>
        <Text>{f.mockSuccess}</Text>
        <Text fw={500}>{ticket.summary}</Text>
        <Paper bg="dark.6" withBorder={false} p="md">
          <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
            {ticket.description}
          </Text>
        </Paper>
        <Group justify="flex-end">
          <Button variant="subtle" color="gray" onClick={onReset}>{t.editor.reset}</Button>
        </Group>
      </Stack>
    );
  }

  return (
    <Stack>
      <Title order={3}>{t.editor.title}</Title>
      <Text>{f.required}</Text>
      {ticket && <Alert>{f.mockSuccess} {ticket.key}. {f.consumed}</Alert>}

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

      {review && <Paper withBorder p="sm"><Stack>
        <Text>{c.review}</Text>
        {Object.entries(review.draft).map(([key, value]) => <Text key={key} style={{ whiteSpace: 'pre-wrap' }}>{label(key)}: {value}</Text>)}
        {review.answers?.map((a) => <Text key={a.question_id}>{a.answer}</Text>)}
        {review.history?.map((q) => <Text key={q.id}>{q.text} · {q.answer} · {q.rationale} · {q.evidence}</Text>)}
        {review.proposals?.map((p) => <Text key={p.id}>{p.replacement} · {p.reason}</Text>)}
        <Button disabled={loading} onClick={() => {
          invalidate();
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
          invalidate(); includedText.current = null; setInclusion(null); setSubmissionPreview(null);
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
      {Object.keys(answers).length > 0 && <Alert color="yellow">
        {f.pending}
        <Button variant="default" disabled={disabled} onClick={() => {
          invalidate(); answerVersion.current += 1; setAnswers({});
        }}>{f.discard}</Button>
      </Alert>}
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
        <Text>{f.choose}</Text><Text style={{ whiteSpace: 'pre-wrap' }}>{submissionPreview}</Text>
        <Button disabled={disabled} onClick={() => {
          invalidate(); setInclusion(true); setSubmissionPreview(null); return handlePrepare(true);
        }}>{c.include}</Button>
        <Button disabled={disabled} variant="default" onClick={() => {
          invalidate(); setInclusion(false); setSubmissionPreview(null); return handlePrepare(false);
        }}>{c.decline}</Button>
        <Button variant="subtle" onClick={() => setSubmissionPreview(null)}>{c.cancel}</Button>
      </Stack></Paper>}

      {prepared && <Paper withBorder p="sm"><Stack>
        <Title order={3}>{f.review} {prepared.revision}</Title>
        <Title order={4}>{f.summary}</Title>
        <Text style={{ whiteSpace: 'pre-wrap' }}>{serializeFinal(prepared, 'summary')}</Text>
        <Text style={{ whiteSpace: 'pre-wrap' }}>{serializeFinal(prepared)}</Text>
        <Text>{f.unresolved}</Text>
        {prepared.questions.filter((q) => !q.superseded_by && !['answered', 'not_applicable'].includes(q.status)).map((q) =>
          <Text key={q.id}>{q.text} · {c.statuses[q.status]} · {c.priorities[q.priority]} · {q.rationale}</Text>)}
        {prepared.open_question_items.map((item, index) => <Text key={index}>{item}</Text>)}
        {prepared.proposals.length > 0 && <Alert color="yellow">{f.proposals}</Alert>}
        {prepared.open_question_items.length > 0 && <Button disabled={disabled} variant="default" onClick={() => {
          invalidate(); setInclusion(null);
          if (includedText.current?.after === local.current.open_questions) {
            replaceDraft({ ...local.current, open_questions: includedText.current.before });
          }
          includedText.current = null;
          setSubmissionPreview(openQuestionsPreview(local.current.open_questions, checkpoint.open_question_items));
        }}>{f.inclusion}</Button>}
        <div className="export-toolbar" role="group" aria-label={f.toolbar}>
          {['summary', 'description', 'markdown'].map((format) => {
            const title = format === 'summary' ? f.copySummary : format === 'description' ? f.copyDescription : f.download;
            return <Button key={format} className="export-action" variant="subtle" color="gray" aria-label={title}
              disabled={disabled} onClick={format === 'markdown' ? handleDownload : () => handleCopy(format)}>
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true" focusable="false">
                {format === 'markdown' ? <path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5" />
                  : <g><rect x="8" y="8" width="12" height="13" rx="2" /><path d="M16 8V3H3v13h5" /></g>}
              </svg>
              <span>{title}</span>
            </Button>;
          })}
        </div>
      </Stack></Paper>}
      {notice && <Alert color={notificationColors[notice.type]} variant="light" role="status">{notice.message}</Alert>}
      <Group className="editor-analysis-actions" justify="space-between" wrap="wrap">
        <Button disabled={disabled} onClick={() => operate('analyze')}>{checkpoint ? c.again : c.analyze}</Button>
        <Button className="session-check-action" variant="subtle" color="gray"
          disabled={loading || !sessionId} onClick={recover}
          leftSection={<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
            <path d="M20 7v5h-5M4 17v-5h5" />
            <path d="M6.1 7a7 7 0 0 1 11.55-1.6L20 8M4 16l2.35 2.6A7 7 0 0 0 17.9 17" />
          </svg>}>
          {c.recover}
        </Button>
      </Group>
      <Group justify="space-between">
        {prepared ? <Text c="green" role="status">{f.ready}</Text> : <Button onClick={() => handlePrepare()} disabled={disabled || missing.length > 0 || review !== null || Object.keys(answers).length > 0}>
          {everPrepared ? f.update : f.prepare}
        </Button>}
        <Button
          onClick={handleCreate}
          loading={loading}
          disabled={disabled || !prepared || review !== null}
        >
          {t.editor.create}
        </Button>
      </Group>
      <Text size="sm" c="dimmed">{f.mockNote}</Text>
      <Group className="reset-action" justify="flex-start">
        <Button variant="subtle" color="gray" onClick={onReset} disabled={loading}>{t.editor.reset}</Button>
      </Group>
    </Stack>
  );
}

export default DraftEditor;
