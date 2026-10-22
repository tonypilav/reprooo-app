import { useState } from "react";
import { generateDraft, getTemplate, maskPreview } from "./api";
import DraftEditor from "./DraftEditor";

function App() {
  const [text, setText] = useState("");
  const [sessionId, setSessionId] = useState(null);
  const [maskedText, setMaskedText] = useState("");
  const [draftResult, setDraftResult] = useState(null);
  const [template, setTemplate] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const step = draftResult ? "draft" : sessionId ? "preview" : "input";

  async function handleMask() {
    setLoading(true);
    setError("");
    try {
      const data = await maskPreview(text);
      setSessionId(data.session_id);
      setMaskedText(data.masked_text);
    } catch {
      setError("Не удалось замаскировать текст. Проверьте, что бэкенд запущен.");
    } finally {
      setLoading(false);
    }
  }

  async function handleGenerate() {
    setLoading(true);
    setError("");
    try {
      const data = await generateDraft(sessionId, maskedText);
      const loadedTemplate = await getTemplate("bug");
      setDraftResult(data);
      setTemplate(loadedTemplate);
    } catch (e) {
      if (e.message === "SESSION_EXPIRED") {
        setError("Сессия истекла. Вернитесь назад и замаскируйте текст заново.");
      } else {
        setError("Не удалось сгенерировать черновик.");
      }
    } finally {
      setLoading(false);
    }
  }

  function handleReset() {
    setSessionId(null);
    setMaskedText("");
    setDraftResult(null);
    setTemplate(null);
    setError("");
  }

  return (
    <div>
      <h1>Reprooo</h1>

      {error && <p>{error}</p>}

      {step === "input" && (
        <div>
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
        </div>
      )}

      {step === "preview" && (
        <div>
          <h2>Что уйдёт в LLM</h2>
          <p>
            Проверьте текст. Метки вроде [EMAIL_1] нужны, чтобы вернуть настоящие
            данные в черновике, поэтому не удаляйте их.
          </p>
          <textarea
            value={maskedText}
            onChange={(event) => setMaskedText(event.target.value)}
            rows={10}
            cols={60}
          />
          <div>
            <button onClick={handleReset} disabled={loading}>
              Назад
            </button>
            <button
              onClick={handleGenerate}
              disabled={loading || !maskedText.trim()}
            >
              {loading ? "Генерирую..." : "Сгенерировать черновик"}
            </button>
          </div>
        </div>
      )}

      {step === "draft" && (
        <DraftEditor
          template={template}
          initialDraft={draftResult.draft}
          sessionId={sessionId}
          onReset={handleReset}
        />
      )}
    </div>
  );
}

export default App;
