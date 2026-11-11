import { t } from "./texts.js";

const sections = [
  ['summary', 'Краткое описание'],
  ['business_context', 'Бизнес-контекст'],
  ['requirements', 'Требования'],
  ['acceptance_criteria', 'Критерии приёмки'],
  ['open_questions', 'Открытые вопросы'],
];

export function requiredFinalFields(draft) {
  return ['summary', 'business_context', 'acceptance_criteria']
    .filter((key) => !draft[key]?.trim());
}

export function finalSnapshot(checkpoint, inclusion) {
  if (requiredFinalFields(checkpoint.draft).length) return null;
  const freeze = (value) => {
    if (value && typeof value === 'object') {
      Object.values(value).forEach(freeze);
      Object.freeze(value);
    }
    return value;
  };
  return freeze(structuredClone({ draft: checkpoint.draft, revision: checkpoint.revision,
    questions: checkpoint.question_history || checkpoint.questions || [],
    open_question_items: checkpoint.open_question_items || [],
    proposals: checkpoint.proposals || [], inclusion }));
}

// Serialization never mutates the canonical draft or the reviewed snapshot.
export function serializeFinal(snapshot, format = 'description') {
  if (format === 'summary') return snapshot.draft.summary.trim();
  if (format === 'markdown') {
    return sections.map(([key, label]) => `## ${label}\n\n${snapshot.draft[key]}`).join('\n\n');
  }
  return sections.filter(([key]) => key !== 'summary' && snapshot.draft[key].trim())
    .map(([key, label]) => `${label}:\n${snapshot.draft[key].trim()}`).join('\n\n');
}

export const notificationColors = { success: 'green', error: 'red', info: 'gray' };
export function finalNotification(kind) {
  const messages = { copied: ['success', t.finalization.copied],
    copyFailed: ['error', t.finalization.copyFailed], downloadFailed: ['error', t.finalization.downloadFailed] };
  const [type, message] = messages[kind];
  return { type, message };
}

export async function copyFinal(snapshot, format) {
  await navigator.clipboard.writeText(serializeFinal(snapshot, format));
}

export function downloadFinal(snapshot) {
  const url = URL.createObjectURL(new Blob([serializeFinal(snapshot, 'markdown')], { type: 'text/markdown;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = 'requirements.md';
  try { link.click(); }
  finally { URL.revokeObjectURL(url); }
}
