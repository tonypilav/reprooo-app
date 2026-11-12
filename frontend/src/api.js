import { resolveApiBaseUrl } from './apiConfig.js';

const API_URL = resolveApiBaseUrl(import.meta.env?.VITE_API_BASE_URL, import.meta.env?.PROD);

export function isSessionUnavailable(error) {
  return error?.code === "SESSION_UNAVAILABLE";
}

async function sessionResponse(response) {
  if (!response.ok) {
    let code;
    try {
      const body = await response.json();
      code = body?.detail?.code;
    } catch {
      // Non-JSON server failures retain ordinary retry behavior.
    }
    const error = new Error("Запрос завершился ошибкой");
    if (code === "SESSION_UNAVAILABLE" && response.status === 404) {
      error.code = code;
    }
    if ((response.status === 502 && code === "LLM_GENERATION_FAILED")
        || (response.status === 504 && code === "LLM_TIMEOUT")) {
      error.code = code;
    }
    if (response.status === 409 && code === "REVISION_CONFLICT") error.code = code;
    const expected = { RATE_LIMITED: 429, LLM_CAPACITY_EXHAUSTED: 503,
      OPERATION_CAPACITY_EXHAUSTED: 503, RATE_LIMIT_CAPACITY_EXHAUSTED: 503,
      DEMO_BUDGET_EXHAUSTED: 503, CAPACITY_EXHAUSTED: 503,
      SESSION_LLM_LIMIT_REACHED: 409, MASKING_LIMIT_REACHED: 409,
      LLM_INPUT_LIMIT_REACHED: 409, REFINEMENT_LIMIT_REACHED: 409,
      REQUEST_TOO_LARGE: 413 };
    if (expected[code] === response.status) error.code = code;
    const seconds = Number(response.headers?.get('Retry-After'));
    if (Number.isInteger(seconds) && seconds > 0 && seconds <= 60) error.retryAfter = seconds;
    throw error;
  }
  return response.json();
}

function requireSession(sessionId) {
  if (!sessionId) {
    const error = new Error("Session unavailable");
    error.code = "SESSION_UNAVAILABLE";
    throw error;
  }
}

export async function getMaskRules() {
  const response = await fetch(`${API_URL}/api/mask-rules`);
  if (!response.ok) {
    throw new Error("Не удалось загрузить правила маскирования");
  }
  return response.json();
}

export async function maskPreview(text, enabledRules) {
  const response = await fetch(`${API_URL}/api/mask-preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, enabled_rules: enabledRules }),
  });
  return sessionResponse(response);
}

export async function generateDraft(sessionId, maskedText) {
  requireSession(sessionId);
  const response = await fetch(`${API_URL}/api/generate-draft`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, masked_text: maskedText }),
  });
  return sessionResponse(response);
}

function templateReference(reference) {
  if (!reference || typeof reference.mode !== "string" || !reference.mode
      || !Number.isInteger(reference.version) || reference.version < 1) {
    throw new Error("Template metadata is unavailable");
  }
  return { mode: reference.mode, version: reference.version };
}

export async function getTemplate(reference) {
  const expected = templateReference(reference);
  const response = await fetch(`${API_URL}/api/templates/${encodeURIComponent(expected.mode)}`);
  if (!response.ok) {
    throw new Error("Не удалось загрузить шаблон");
  }
  const loaded = await response.json();
  if (loaded.mode !== expected.mode || loaded.version !== expected.version
      || !Array.isArray(loaded.fields)) {
    throw new Error("Template metadata does not match the draft");
  }
  return loaded;
}

export async function createTicket(sessionId, draft, template, synchronization = {}) {
  requireSession(sessionId);
  const response = await fetch(`${API_URL}/api/create-ticket`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, template: templateReference(template), draft, ...synchronization }),
  });
  return sessionResponse(response);
}

export async function clarificationOperation(kind, payload) {
  requireSession(payload.session_id);
  return sessionResponse(await fetch(`${API_URL}/api/clarifications/${kind}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }));
}

export async function getRefinement(sessionId) {
  requireSession(sessionId);
  return sessionResponse(await fetch(`${API_URL}/api/sessions/${encodeURIComponent(sessionId)}/refinement`));
}

export function resourceErrorMessage(error) {
  if (error?.code === 'RATE_LIMITED') return error.retryAfter
    ? `Слишком много запросов. Повторите через ${error.retryAfter} сек.`
    : 'Слишком много запросов. Повторите через минуту.';
  const messages = {
    LLM_CAPACITY_EXHAUSTED: 'AI занят. Ваши правки сохранены. Повторите чуть позже.',
    OPERATION_CAPACITY_EXHAUSTED: 'Сервер занят. Повторите чуть позже.',
    RATE_LIMIT_CAPACITY_EXHAUSTED: 'Сервер занят. Повторите через минуту.',
    DEMO_BUDGET_EXHAUSTED: 'Лимит AI для демо исчерпан. Можно продолжить просмотр и экспорт.',
    SESSION_LLM_LIMIT_REACHED: 'Лимит AI этой сессии исчерпан. Можно продолжить просмотр и экспорт.',
    CAPACITY_EXHAUSTED: 'Хранилище сервера заполнено. Повторите позже.',
    MASKING_LIMIT_REACHED: 'Достигнут лимит маскирования. Состояние сессии сохранено.',
    REFINEMENT_LIMIT_REACHED: 'Достигнут лимит уточнений. Текущий черновик сохранён.',
    LLM_INPUT_LIMIT_REACHED: 'Слишком большой объём данных для AI. Сократите текст.',
    REQUEST_TOO_LARGE: 'Слишком большой запрос. Сократите текст.',
  };
  return messages[error?.code];
}
