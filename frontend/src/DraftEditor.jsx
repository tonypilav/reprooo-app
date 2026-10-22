import { useState } from "react";
import { createTicket } from "./api";

function DraftEditor({ template, initialDraft, sessionId, onReset }) {
  const [draft, setDraft] = useState(initialDraft);
  const [ticket, setTicket] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const missing = template.fields
    .filter((field) => field.required && !(draft[field.key] || "").trim())
    .map((field) => field.key);

  function handleChange(key, value) {
    setDraft({ ...draft, [key]: value });
  }

  async function handleCreate() {
    setLoading(true);
    setError("");
    try {
      const result = await createTicket(sessionId, draft);
      setTicket(result);
    } catch {
      setError("Не удалось создать тикет.");
    } finally {
      setLoading(false);
    }
  }

  if (ticket) {
    return (
      <div>
        <h2>Тикет создан: {ticket.key}</h2>
        <p>{ticket.summary}</p>
        <pre style={{ whiteSpace: "pre-wrap" }}>{ticket.description}</pre>
        <button onClick={onReset}>Начать заново</button>
      </div>
    );
  }

  return (
    <div>
      <h2>Черновик тикета</h2>
      {error && <p>{error}</p>}

      {template.fields.map((field) => {
        const isMissing = missing.includes(field.key);
        return (
          <div key={field.key}>
            <label>
              <strong>
                {field.label}
                {field.required ? " *" : ""}
              </strong>
              <div>{field.hint}</div>
              <textarea
                value={draft[field.key] || ""}
                onChange={(event) => handleChange(field.key, event.target.value)}
                rows={field.key === "title" ? 2 : 4}
                cols={60}
                style={{
                  display: "block",
                  border: isMissing ? "2px solid crimson" : undefined,
                }}
              />
            </label>
            {isMissing && <div>Заполните это поле</div>}
          </div>
        );
      })}

      <button onClick={onReset} disabled={loading}>
        Начать заново
      </button>
      <button onClick={handleCreate} disabled={loading || missing.length > 0}>
        {loading ? "Создаю..." : "Создать тикет"}
      </button>
    </div>
  );
}

export default DraftEditor;