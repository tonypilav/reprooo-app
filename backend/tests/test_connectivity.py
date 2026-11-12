"""Synthetic connectivity configuration and actual application CORS checks."""
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import httpx2
from app import config, main


class ConnectivityTests(unittest.IsolatedAsyncioTestCase):
    def configured(self, environment):
        return patch.dict(os.environ, environment, clear=True)

    def test_local_default(self):
        with self.configured({}):
            self.assertEqual(config.get_cors_origins(), ["http://localhost:5173"])

    def test_explicit_allowlist(self):
        with self.configured({"APP_ENV": "production", "CORS_ALLOWED_ORIGINS":
                              "https://demo.example.com, https://other.example.com"}):
            self.assertEqual(config.get_cors_origins(),
                             ["https://demo.example.com", "https://other.example.com"])

    def test_malformed_configuration(self):
        for origin in ["", "*", "null", "https://*.vercel.app", "https://example.com/",
                       "https://example.com/path", "https://example.com?", "https://example.com#",
                       "https://user:pass@example.com", "https://example.com:bad",
                       "https://example.com:99999", "https://example.com:",
                       "https://bad host", "https://bad_host", "https://example.com,",
                       "https://example.com\\path"]:
            with self.subTest(origin=origin), self.configured({"CORS_ALLOWED_ORIGINS": origin}):
                with self.assertRaises(ValueError) as error:
                    config.get_cors_origins()
                if origin:
                    self.assertNotIn(origin, str(error.exception))

    def test_invalid_production_prevents_application_initialization(self):
        for environment in [{"APP_ENV": "invalid"}, {"APP_ENV": "production"},
                            {"APP_ENV": "production", "CORS_ALLOWED_ORIGINS": "http://localhost:5173"},
                            {"APP_ENV": "production", "CORS_ALLOWED_ORIGINS": "http://demo.example.com"}]:
            with self.subTest(environment=environment), self.configured(environment):
                with self.assertRaises(ValueError):
                    exec(compile(Path(main.__file__).read_text(), main.__file__, 'exec'),
                         {"__name__": "synthetic_connectivity"})

    async def test_cors_actual_application(self):
        for environment, allowed in [({}, "http://localhost:5173"),
                ({"APP_ENV": "production", "CORS_ALLOWED_ORIGINS": "https://demo.example.com"},
                 "https://demo.example.com")]:
            with self.configured(environment):
                namespace = {"__name__": "synthetic_connectivity"}
                # No live provider is constructed in this test.
                with patch('app.llm_client.create_llm_client', return_value=object()):
                    exec(compile(Path(main.__file__).read_text(), main.__file__, 'exec'), namespace)
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=namespace['app']),
                                          base_url="http://synthetic.test") as client:
                for origin in [allowed, "https://unlisted.vercel.app", "https://disallowed.example.com"]:
                    response = await client.get('/api/health', headers={"Origin": origin})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.headers.get('access-control-allow-origin'),
                                     allowed if origin == allowed else None)
                    self.assertNotIn('access-control-allow-credentials', response.headers)
                    preflight = await client.options('/api/health', headers={
                        "Origin": origin, "Access-Control-Request-Method": "POST",
                        "Access-Control-Request-Headers": "content-type"})
                    self.assertEqual(preflight.status_code, 200 if origin == allowed else 400)
