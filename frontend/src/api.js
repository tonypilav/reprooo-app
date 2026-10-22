const API_URL = "http://localhost:8000";

export async function maskPreview(text) {
  const response = await fetch(`${API_URL}/api/mask-preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!response.ok) {
    throw new Error("Запрос завершился ошибкой");
  }
  return response.json();
}

export async function generateDraft(sessionId, maskedText) {
  const response = await fetch(`${API_URL}/api/generate-draft`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, masked_text: maskedText }),
  });
  if (response.status === 404) {
    throw new Error("SESSION_EXPIRED");
  }
  if (!response.ok) {
    throw new Error("Запрос завершился ошибкой");
  }
  return response.json();
}