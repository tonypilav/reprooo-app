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