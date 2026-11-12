import assert from 'node:assert/strict';
import { test, mock, afterEach } from 'node:test';
import { resolveApiBaseUrl } from './apiConfig.js';
import { getMaskRules } from './api.js';

afterEach(() => mock.restoreAll());
test('local default requires no configuration', async () => {
  assert.equal(resolveApiBaseUrl(undefined), 'http://localhost:8000');
  mock.method(globalThis, 'fetch', async (url) => {
    assert.equal(url, 'http://localhost:8000/api/mask-rules');
    return { ok: true, json: async () => [] };
  });
  await getMaskRules();
});
test('configured production URL and trailing slashes normalize', () => {
  assert.equal(resolveApiBaseUrl('https://api.example.com', true), 'https://api.example.com');
  assert.equal(resolveApiBaseUrl(' https://api.example.com/base/// ', true), 'https://api.example.com/base');
});
for (const value of [undefined, '', ' ', '/api', 'invalid', 'https:api.example.com', 'http://api.example.com',
  'https://localhost', 'https://127.0.0.1', 'https://[::1]', 'ftp://api.example.com',
  'https://user:pass@api.example.com', 'https://api.example.com?q=1',
  'https://api.example.com#fragment', 'https://api.example.com\\path']) {
  test(`invalid production URL rejected: ${value}`, () => {
    assert.throws(() => resolveApiBaseUrl(value, true), /VITE_API_BASE_URL/);
  });
}
test('invalid configured development URL is rejected', () => {
  assert.throws(() => resolveApiBaseUrl('invalid'), /VITE_API_BASE_URL/);
});
