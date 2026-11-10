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

export async function getTemplate(mode) {
  const response = await fetch(`${API_URL}/api/templates/${mode}`);
  if (!response.ok) {
    throw new Error("Не удалось загрузить шаблон");
  }
  return response.json();
}

export async function createTicket(sessionId, draft) {
  requireSession(sessionId);
  const response = await fetch(`${API_URL}/api/create-ticket`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, draft }),
  });
  return sessionResponse(response);
}