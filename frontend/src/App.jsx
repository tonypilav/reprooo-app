import { useState } from "react";
import { maskPreview } from "./api";

function App() {
  const [text, setText] = useState("");
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleMask() {
    setLoading(true);
    setError("");
    try {
      const data = await maskPreview(text);
      setResult(data);
    } catch {
      setError("Не удалось замаскировать текст. Проверьте, что бэкенд запущен.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      <h1>Reprooo</h1>

      <textarea
        value={text}
        onChange={(event) => setText(event.target.value)}
        rows={10}
        cols={60}
        placeholder="Вставьте текст треда"
      />

      <div>
        <button onClick={handleMask} disabled={loading || !text.trim()}>
          {loading ? "Маскирую..." : "Замаскировать"}
        </button>
      </div>

      {error && <p>{error}</p>}

      {result && (
        <div>
          <h2>Что уйдёт в LLM</h2>
          <pre style={{ whiteSpace: "pre-wrap" }}>{result.masked_text}</pre>
        </div>
      )}
    </div>
  );
}

export default App;
