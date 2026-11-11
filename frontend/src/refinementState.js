// Keep edits made after a request started, independently for each section.
export function mergeCheckpoint(local, submitted, canonical) {
  return Object.fromEntries(Object.keys(local).map((key) =>
    [key, local[key] === submitted[key] ? canonical[key] : local[key]]));
}

export function openQuestionsPreview(current, items = []) {
  const normalize = (text) => text.toLocaleLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
  const seen = normalize(current);
  const additions = [...new Set(items)].filter((item) => !seen.includes(normalize(item.replace(/^Решение делегировано реализации: /, ''))));
  return [current, ...additions.map((item) => `- ${item}`)].filter(Boolean).join('\n');
}
