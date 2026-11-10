import { useState } from "react";
import {
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
import { createTicket, isSessionUnavailable } from "./api";
import { t } from "./texts";

function DraftEditor({ template, initialDraft, sessionId, onReset, onSessionUnavailable }) {
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
    if (!sessionId) return;
    setLoading(true);
    setError("");
    try {
      const result = await createTicket(sessionId, draft);
      setTicket(result);
    } catch (error) {
      if (isSessionUnavailable(error)) {
        onSessionUnavailable();
      } else {
        setError(t.errors.createTicket);
      }
    } finally {
      setLoading(false);
    }
  }

  if (ticket) {
    return (
      <Stack>
        <Group>
          <Title order={3}>{t.editor.created}</Title>
          <Badge size="lg">{ticket.key}</Badge>
        </Group>
        <Text fw={500}>{ticket.summary}</Text>
        <Paper bg="dark.6" withBorder={false} p="md">
          <Text size="sm" style={{ whiteSpace: "pre-wrap" }}>
            {ticket.description}
          </Text>
        </Paper>
        <Group justify="flex-end">
          <Button onClick={onReset}>{t.editor.reset}</Button>
        </Group>
      </Stack>
    );
  }

  return (
    <Stack>
      <Title order={3}>{t.editor.title}</Title>

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
          minRows={field.key === "title" ? 1 : 3}
        />
      ))}

      <Group justify="space-between">
        <Button variant="default" onClick={onReset} disabled={loading}>
          {t.editor.reset}
        </Button>
        <Button
          onClick={handleCreate}
          loading={loading}
          disabled={!sessionId || missing.length > 0}
        >
          {t.editor.create}
        </Button>
      </Group>
    </Stack>
  );
}

export default DraftEditor;
