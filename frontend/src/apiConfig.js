// Shared by the client and Vite so invalid production settings fail at build time.
export function resolveApiBaseUrl(value, production = false) {
  const configured = value?.trim();
  if (!configured && !production) return 'http://localhost:8000';
  const invalid = () => new Error('VITE_API_BASE_URL must be an explicit valid HTTP(S) URL (HTTPS in production)');
  if (!configured || !/^https?:\/\//.test(configured)) throw invalid();
  let url;
  try { url = new URL(configured); } catch { throw invalid(); }
  if (!['http:', 'https:'].includes(url.protocol)
      || (production && (url.protocol !== 'https:' || ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)))
      || url.username || url.password || url.search || url.hash
      || /[\s\\?#]/.test(configured)) throw invalid();
  return url.href.replace(/\/+$/, '');
}
