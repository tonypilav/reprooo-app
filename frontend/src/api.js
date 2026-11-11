const API_URL = "http://localhost:8000";

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
  if (!response.ok) {
    throw new Error("Запрос завершился ошибкой");
  }
  return response.json();
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

export async function createTicket(sessionId, draft, template) {
  requireSession(sessionId);
  const response = await fetch(`${API_URL}/api/create-ticket`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, template: templateReference(template), draft }),
  });
  return sessionResponse(response);
}