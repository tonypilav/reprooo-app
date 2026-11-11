import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { afterEach, mock, test } from "node:test";
import { transformWithOxc } from "vite";
import * as api from "./api.js";
import { t } from "./texts.js";

const sid = "A".repeat(22);
const reference = { mode: "requirements", version: 1 };
const summaryLabel = "Краткое описание";
const requirementsTemplate = {
  ...reference,
  fields: [
    { key: "summary", label: summaryLabel, required: true, hint: "" },
    { key: "business_context", label: "Бизнес-контекст", required: true, hint: "" },
    { key: "requirements", label: "Требования", required: false, hint: "" },
    { key: "acceptance_criteria", label: "Критерии приёмки", required: true, hint: "" },
    { key: "open_questions", label: "Открытые вопросы", required: false, hint: "" },
  ],
};
const completeDraft = () => ({
  summary: "Synthetic summary", business_context: "Synthetic motivation",
  requirements: "", acceptance_criteria: "Synthetic condition", open_questions: "",
});
const unavailable = { detail: { code: "SESSION_UNAVAILABLE", message: "untrusted message" } };
const response = (status, body) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
afterEach(() => mock.restoreAll());

for (const operation of [api.generateDraft, api.createTicket]) {
  test(`${operation.name}: recognizes unavailable-session code`, async () => {
    mock.method(globalThis, "fetch", async () => response(404, unavailable));
    await assert.rejects(operation(sid, "synthetic", reference), (error) => {
      assert.equal(api.isSessionUnavailable(error), true);
      assert.equal(error.message.includes("untrusted"), false);
      return true;
    });
  });
  test(`${operation.name}: unrelated 404 is retryable`, async () => {
    mock.method(globalThis, "fetch", async () => response(404, { detail: "Not found" }));
    await assert.rejects(operation(sid, "synthetic", reference), (error) => !api.isSessionUnavailable(error));
  });
  test(`${operation.name}: invalid local session never fetches`, async () => {
    const fetch = mock.method(globalThis, "fetch", async () => assert.fail("Must not fetch"));
    await assert.rejects(operation(null, "synthetic"), api.isSessionUnavailable);
    assert.equal(fetch.mock.callCount(), 0);
  });
}

test("non-JSON failure does not become a session error", async () => {
  mock.method(globalThis, "fetch", async () => ({ ok: false, status: 500, json: async () => { throw new Error("HTML"); } }));
  await assert.rejects(api.generateDraft(sid, "synthetic"), (error) => !api.isSessionUnavailable(error));
});
test("wrong HTTP status with session-like body is not misclassified", async () => {
  mock.method(globalThis, "fetch", async () => response(500, unavailable));
  await assert.rejects(api.generateDraft(sid, "synthetic"), (error) => !api.isSessionUnavailable(error));
});
test("successful preview-review and ticket shapes are preserved", async () => {
  const review = { status: "preview_review_required", masked_text: "synthetic masked text" };
  mock.method(globalThis, "fetch", async () => response(200, review));
  assert.deepEqual(await api.generateDraft(sid, "synthetic"), review);
  const ticket = { key: "MOCK-1", description: "Synthetic ticket" };
  globalThis.fetch.mock.restore();
  mock.method(globalThis, "fetch", async () => response(200, ticket));
  assert.deepEqual(await api.createTicket(sid, {}, reference), ticket);
});

// Run actual JSX handlers using existing Vite and a minimal hook/tree harness.
// This checks component logic, not browser layout or React scheduling.
async function mount(filename) {
  let cursor = 0;
  let mounted = false;
  const states = [];
  const effects = [];
  const pending = [];
  const createElement = (type, props, ...children) => ({ type, props: props || {}, children });
  const deps = {
    ...api, t, createElement,
    getMaskRules(...args) {
      const result = api.getMaskRules(...args);
      pending.push(result);
      return result;
    },
    useState(initial) {
      const index = cursor++;
      if (!(index in states)) states[index] = initial;
      return [states[index], (value) => {
        states[index] = typeof value === "function" ? value(states[index]) : value;
      }];
    },
    useEffect(effect) { if (!mounted) effects.push(effect); },
    Stepper: Object.assign(() => {}, { Step: "Step" }),
    MaskRuleToggles: "MaskRuleToggles", DraftEditor: "DraftEditor", logo: "synthetic-logo",
  };
  for (const name of ["Alert", "Box", "Button", "Container", "Group", "Image", "Paper", "Stack", "Text", "Textarea", "Title", "Badge"]) deps[name] = name;
  const source = (await readFile(new URL(filename, import.meta.url), "utf8"))
    .replace(/^import[\s\S]*?from\s+["'][^"']+["'];/gm, "");
  const { code } = await transformWithOxc(source, filename, { jsx: { runtime: "classic", pragma: "createElement" } });
  const componentName = filename.includes("DraftEditor") ? "DraftEditor" : "App";
  const factoryCode = code.replace(`export default ${componentName};`, `return ${componentName};`);
  if (componentName === "DraftEditor") delete deps.DraftEditor;
  const Component = new Function(...Object.keys(deps), factoryCode)(...Object.values(deps));
  return {
    render(props = {}) { cursor = 0; const tree = Component(props); mounted = true; return tree; },
    async effects() { for (const effect of effects.splice(0)) effect(); await Promise.all(pending); },
  };
}
function nodes(tree) {
  if (Array.isArray(tree)) return tree.flatMap(nodes);
  if (!tree || typeof tree !== "object") return [];
  return [tree, ...tree.children.flatMap(nodes)];
}
function find(tree, type, text) {
  const node = nodes(tree).find((node) => node.type === type && (text === undefined || node.children.includes(text) || node.props.label === text));
  assert.ok(node, `Missing ${type}: ${text}`);
  return node;
}
const change = (node, value) => node.props.onChange({ currentTarget: { value } });

async function previewApp(generationResponse, templateResponse = response(200, requirementsTemplate)) {
  const rules = [{ key: "secrets", enabled_by_default: true, locked: true }, { key: "emails", enabled_by_default: true }];
  const fetch = mock.method(globalThis, "fetch", async (url) => {
    if (url.endsWith("mask-rules")) return response(200, rules);
    if (url.endsWith("mask-preview")) return response(200, { session_id: sid, masked_text: "Synthetic preview" });
    if (url.endsWith("generate-draft")) return generationResponse;
    if (url.includes("templates/")) return typeof templateResponse === "function" ? templateResponse() : templateResponse;
    if (url.endsWith("create-ticket")) return response(404, unavailable);
    assert.fail(`Unexpected request: ${url}`);
  });
  const app = await mount("./App.jsx");
  app.render();
  await app.effects();
  change(find(app.render(), "Textarea", t.input.title), "Original synthetic context");
  find(app.render(), "MaskRuleToggles").props.onChange(["secrets"]);
  await find(app.render(), "Button", t.input.action).props.onClick();
  return { app, fetch };
}

test("preview recovery preserves text/rules, disables requests, and restarts explicitly", async () => {
  const { app, fetch } = await previewApp(response(404, unavailable));
  change(find(app.render(), "Textarea", t.preview.title), "User edited preview");
  await find(app.render(), "Button", t.preview.action).props.onClick();
  let tree = app.render();
  assert.equal(find(tree, "Textarea", t.preview.title).props.value, "User edited preview");
  assert.equal(find(tree, "Button", t.preview.action).props.disabled, true);
  assert.ok(nodes(tree).some((node) => node.children.includes(t.errors.sessionUnavailable)));
  const calls = fetch.mock.callCount();
  await find(tree, "Button", t.preview.action).props.onClick();
  assert.equal(fetch.mock.callCount(), calls);
  find(tree, "Button", t.editor.reset).props.onClick();
  tree = app.render();
  assert.equal(find(tree, "Textarea", t.input.title).props.value, "Original synthetic context");
  assert.deepEqual(find(tree, "MaskRuleToggles").props.value, ["secrets"]);
  assert.equal(nodes(tree).some((node) => node.type === "DraftEditor"), false);
});
test("unrelated generation errors preserve normal retry", async () => {
  const { app } = await previewApp(response(404, { detail: "Another missing resource" }));
  await find(app.render(), "Button", t.preview.action).props.onClick();
  const tree = app.render();
  assert.equal(find(tree, "Button", t.preview.action).props.disabled, false);
  assert.equal(find(tree, "Textarea", t.preview.title).props.value, "Synthetic preview");
});
test("ticket recovery preserves edited draft, clears session, disables resubmission", async () => {
  const { app, fetch } = await previewApp(response(200, { template: reference, draft: completeDraft(), missing_fields: [] }));
  await find(app.render(), "Button", t.preview.action).props.onClick();
  const editor = await mount("./DraftEditor.jsx");
  let props = find(app.render(), "DraftEditor").props;
  editor.render(props);
  change(find(editor.render(props), "Textarea", summaryLabel), "Edited synthetic title");
  await find(editor.render(props), "Button", t.editor.create).props.onClick();
  props = find(app.render(), "DraftEditor").props;
  assert.equal(props.sessionId, null);
  let tree = editor.render(props);
  assert.equal(find(tree, "Textarea", summaryLabel).props.value, "Edited synthetic title");
  assert.equal(find(tree, "Button", t.editor.create).props.disabled, true);
  const calls = fetch.mock.callCount();
  await find(tree, "Button", t.editor.create).props.onClick();
  assert.equal(fetch.mock.callCount(), calls);
  find(tree, "Button", t.editor.reset).props.onClick();
  tree = app.render();
  assert.equal(find(tree, "Textarea", t.input.title).props.value, "Original synthetic context");
  assert.deepEqual(find(tree, "MaskRuleToggles").props.value, ["secrets"]);
});

test("unrelated submission errors keep draft editable and allow retry", async () => {
  const fetch = mock.method(globalThis, "fetch", async () => response(500, { detail: "Processing failed" }));
  const onUnavailable = mock.fn();
  const editor = await mount("./DraftEditor.jsx");
  const props = {
    template: requirementsTemplate,
    initialDraft: completeDraft(), sessionId: sid,
    onReset: mock.fn(), onSessionUnavailable: onUnavailable,
  };
  editor.render(props);
  change(find(editor.render(props), "Textarea", summaryLabel), "User edited draft");
  await find(editor.render(props), "Button", t.editor.create).props.onClick();
  const tree = editor.render(props);
  assert.equal(onUnavailable.mock.callCount(), 0);
  assert.equal(find(tree, "Textarea", summaryLabel).props.value, "User edited draft");
  assert.equal(find(tree, "Button", t.editor.create).props.disabled, false);
  await find(tree, "Button", t.editor.create).props.onClick();
  assert.equal(fetch.mock.callCount(), 2);
});

test("template lookup follows generation metadata and verifies mode/version", async () => {
  const fetch = mock.method(globalThis, "fetch", async () => response(200, requirementsTemplate));
  assert.deepEqual(await api.getTemplate(reference), requirementsTemplate);
  assert.ok(fetch.mock.calls[0].arguments[0].endsWith("/api/templates/requirements"));
});

test("template mismatches and malformed references fail safely", async () => {
  mock.method(globalThis, "fetch", async () => response(200, { ...requirementsTemplate, version: 2 }));
  await assert.rejects(api.getTemplate(reference));
  globalThis.fetch.mock.restore();
  mock.method(globalThis, "fetch", async () => response(200, { ...requirementsTemplate, mode: "other" }));
  await assert.rejects(api.getTemplate(reference));
  globalThis.fetch.mock.restore();
  const unused = mock.method(globalThis, "fetch", async () => assert.fail("Invalid reference must not fetch"));
  for (const value of [null, {}, { mode: "requirements", version: "1" }, { mode: "requirements", version: true }]) {
    await assert.rejects(api.getTemplate(value));
  }
  assert.equal(unused.mock.callCount(), 0);
});

test("submission serializes the edited draft and only template identity", async () => {
  const fetch = mock.method(globalThis, "fetch", async () => response(200, { key: "MOCK-1" }));
  const draft = completeDraft();
  draft.open_questions = "Which synthetic roles may use this?";
  await api.createTicket(sid, draft, requirementsTemplate);
  assert.deepEqual(JSON.parse(fetch.mock.calls[0].arguments[1].body), { session_id: sid, template: reference, draft });
});

test("five fields are ordered and independently editable; only three gate submission", async () => {
  const initial = { summary: "Add a test-message button", business_context: "", requirements: "Synthetic context", acceptance_criteria: "", open_questions: "" };
  const { app, fetch } = await previewApp(response(200, { template: reference, draft: initial, missing_fields: ["business_context", "acceptance_criteria"] }));
  await find(app.render(), "Button", t.preview.action).props.onClick();
  const editor = await mount("./DraftEditor.jsx");
  const props = find(app.render(), "DraftEditor").props;
  let tree = editor.render(props);
  const fields = nodes(tree).filter((node) => node.type === "Textarea");
  assert.deepEqual(fields.map((node) => node.props.label), requirementsTemplate.fields.map((field) => field.label));
  assert.deepEqual(fields.map((node) => node.props.withAsterisk), [true, true, false, true, false]);
  assert.equal(find(tree, "Button", t.editor.create).props.disabled, true);
  const edited = {};
  for (const field of requirementsTemplate.fields) {
    edited[field.key] = `Edited ${field.key}`;
    change(find(editor.render(props), "Textarea", field.label), edited[field.key]);
  }
  tree = editor.render(props);
  for (const field of requirementsTemplate.fields) assert.equal(find(tree, "Textarea", field.label).props.value, edited[field.key]);
  assert.equal(find(tree, "Button", t.editor.create).props.disabled, false);
  for (const field of requirementsTemplate.fields.filter((field) => field.required)) {
    change(find(editor.render(props), "Textarea", field.label), " \n ");
    assert.equal(find(editor.render(props), "Button", t.editor.create).props.disabled, true);
    change(find(editor.render(props), "Textarea", field.label), edited[field.key]);
  }
  for (const field of requirementsTemplate.fields.filter((field) => !field.required)) {
    change(find(editor.render(props), "Textarea", field.label), "");
    edited[field.key] = "";
    assert.equal(find(editor.render(props), "Button", t.editor.create).props.disabled, false);
  }
  await find(editor.render(props), "Button", t.editor.create).props.onClick();
  const call = fetch.mock.calls.find((call) => call.arguments[0].endsWith("create-ticket"));
  assert.deepEqual(JSON.parse(call.arguments[1].body), { session_id: sid, template: reference, draft: edited });
});

for (const failure of ["network", "version"]) {
  test(`template ${failure} failure retains draft for retry without regeneration`, async () => {
    let attempts = 0;
    const original = completeDraft();
    const { app, fetch } = await previewApp(
      response(200, { template: reference, draft: original, missing_fields: [] }),
      () => {
        attempts += 1;
        if (attempts === 1) return failure === "network" ? response(500, {}) : response(200, { ...requirementsTemplate, version: 2 });
        return response(200, requirementsTemplate);
      },
    );
    await find(app.render(), "Button", t.preview.action).props.onClick();
    assert.equal(nodes(app.render()).some((node) => node.type === "DraftEditor"), false);
    assert.ok(nodes(app.render()).some((node) => node.children.includes(t.errors.template)));
    await find(app.render(), "Button", t.editor.retryTemplate).props.onClick();
    const props = find(app.render(), "DraftEditor").props;
    assert.deepEqual(props.initialDraft, original);
    assert.equal(props.sessionId, sid);
    assert.equal(fetch.mock.calls.filter((call) => call.arguments[0].endsWith("generate-draft")).length, 1);
    assert.equal(fetch.mock.calls.filter((call) => call.arguments[0].endsWith("mask-preview")).length, 1);
    assert.equal(fetch.mock.calls.filter((call) => call.arguments[0].endsWith("create-ticket")).length, 0);
    assert.equal(attempts, 2);
  });
}

test("missing generation reference blocks editing without fetching a replacement", async () => {
  const { app, fetch } = await previewApp(response(200, { draft: completeDraft(), missing_fields: [] }));
  await find(app.render(), "Button", t.preview.action).props.onClick();
  assert.equal(nodes(app.render()).some((node) => node.type === "DraftEditor"), false);
  assert.ok(nodes(app.render()).some((node) => node.children.includes(t.errors.template)));
  assert.equal(fetch.mock.calls.filter((call) => call.arguments[0].includes("templates/")).length, 0);
});

test("unsupported template lookup preserves pending draft; restart remains explicit", async () => {
  const { app, fetch } = await previewApp(
    response(200, { template: { mode: "unsupported", version: 1 }, draft: completeDraft(), missing_fields: [] }),
    response(404, { detail: "Template not found" }),
  );
  await find(app.render(), "Button", t.preview.action).props.onClick();
  assert.equal(nodes(app.render()).some((node) => node.type === "DraftEditor"), false);
  assert.ok(fetch.mock.calls.some((call) => call.arguments[0].endsWith("/templates/unsupported")));
  const count = fetch.mock.callCount();
  find(app.render(), "Button", t.editor.reset).props.onClick();
  assert.equal(fetch.mock.callCount(), count);
  assert.equal(find(app.render(), "Textarea", t.input.title).props.value, "Original synthetic context");
});
