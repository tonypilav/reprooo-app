import { useState } from "react";
import {
  Alert,
  Box,
  Button,
  Container,
  Group,
  Image,
  Paper,
  Stack,
  Stepper,
  Text,
  Textarea,
  Title,
} from "@mantine/core";
import { generateDraft, getTemplate, maskPreview } from "./api";
import DraftEditor from "./DraftEditor";
import { t } from "./texts";
import logo from "./assets/logo.png";

const STEP_INDEX = { input: 0, preview: 1, draft: 2 };

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
      setError(t.errors.mask);
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
        setError(t.errors.sessionExpired);
      } else {
        setError(t.errors.generate);
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
    <Box bg="dark.8" mih="100vh" py="xl">
      <Container size="sm">
        <Stack gap="xl">
          <Group gap="sm" wrap="nowrap">
            <Image src={logo} alt={t.appName} w={80} h={80} />
            <div>
              <Title order={1}>{t.appName}</Title>
              <Text c="dimmed">{t.tagline}</Text>
            </div>
          </Group>

          <Stepper active={STEP_INDEX[step]} size="sm">
            <Stepper.Step label={t.steps.input} />
            <Stepper.Step label={t.steps.preview} />
            <Stepper.Step label={t.steps.draft} />
          </Stepper>

          {error && (
            <Alert color="red" variant="light">
              {error}
            </Alert>
          )}

          <Paper>
            {step === "input" && (
              <Stack>
                <Textarea
                  label={t.input.title}
                  description={t.input.description}
                  placeholder={t.input.placeholder}
                  value={text}
                  onChange={(event) => setText(event.currentTarget.value)}
                  autosize
                  minRows={8}
                  maxRows={16}
                />
                <Group justify="flex-end">
                  <Button
                    onClick={handleMask}
                    loading={loading}
                    disabled={!text.trim()}
                  >
                    {t.input.action}
                  </Button>
                </Group>
              </Stack>
            )}

            {step === "preview" && (
              <Stack>
                <Textarea
                  label={t.preview.title}
                  description={t.preview.hint}
                  value={maskedText}
                  onChange={(event) => setMaskedText(event.currentTarget.value)}
                  autosize
                  minRows={8}
                  maxRows={16}
                />
                <Group justify="space-between">
                  <Button
                    variant="default"
                    onClick={handleReset}
                    disabled={loading}
                  >
                    {t.preview.back}
                  </Button>
                  <Button
                    onClick={handleGenerate}
                    loading={loading}
                    disabled={!maskedText.trim()}
                  >
                    {t.preview.action}
                  </Button>
                </Group>
              </Stack>
            )}

            {step === "draft" && (
              <DraftEditor
                template={template}
                initialDraft={draftResult.draft}
                sessionId={sessionId}
                onReset={handleReset}
              />
            )}
          </Paper>
        </Stack>
      </Container>
    </Box>
  );
}

export default App;
