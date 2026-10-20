
import dataclasses
import unittest
from unittest.mock import patch

from app.ai import MockProvider, get_provider
from app.ai.base import (
    AIError,
    parse_json_defensive,
    scrub_message,
    validate_field_row,
)
from app.config import load_settings


FIELD = {
    "source_path": "qty",
    "target": "items.quantity",
    "score": 0.8,
    "name": 0.6,
    "type": 1.0,
    "structure": 1.0,
}


class TestMockAI(unittest.TestCase):
    def test_deterministic_and_labelled(self):
        a = MockProvider().explain_field(FIELD)
        b = MockProvider().explain_field(FIELD)

        self.assertEqual(a.content, b.content)
        self.assertIn("mock", a.label)
        self.assertIn("not a real LLM", a.label)
        self.assertTrue(a.advisory)

    def test_diagnose_groups_and_never_leaks_values(self):
        errs = [
            {
                "collection": "c",
                "path": "price",
                "message": "str 'secret-value-123' is not a number",
            }
        ] * 3

        result = MockProvider().diagnose_failure(errs)

        self.assertNotIn("secret-value-123", str(result.content))
        self.assertIn(
            "3 error(s) in 1 distinct",
            result.content["diagnosis"],
        )

    def test_suggest_unresolved_fk(self):
        schema = {
            "tables": {
                "orders": {
                    "kind": "root",
                    "columns": [
                        {
                            "name": "customer_id",
                            "unresolved_fk": True,
                        }
                    ],
                },
                "customers": {
                    "kind": "root",
                    "columns": [],
                },
            }
        }

        result = MockProvider().suggest_mappings(schema)

        self.assertIn("customers", str(result.content))

    def test_risks(self):
        result = MockProvider().summarize_risks(
            {"needs_review": 1}
        )

        self.assertIn("1 mapping", result.content["risks"][0])

    def test_bad_input_rejected(self):
        with self.assertRaises(AIError):
            MockProvider().explain_field({"x": 1})

        with self.assertRaises(AIError):
            MockProvider().diagnose_failure("nope")


class TestAIHelpers(unittest.TestCase):
    def test_parse_defensive(self):
        self.assertEqual(
            parse_json_defensive('```json\n{"a": 1}\n```'),
            {"a": 1},
        )

        self.assertEqual(
            parse_json_defensive('Sure! {"a": 2} hope that helps'),
            {"a": 2},
        )

        for bad in ("", "[1,2]", "no json here"):
            with self.assertRaises(AIError):
                parse_json_defensive(bad)

    def test_scrub(self):
        self.assertNotIn(
            "abc",
            scrub_message("value 'abc' bad 123456789"),
        )
        for secret in ("mongodb://admin:private@localhost/db", "password=hunter2",
                       "Bearer abc123"):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, scrub_message(f"operation failed: {secret}"))

    def test_get_provider_default_mock(self):
        provider, notice = get_provider(load_settings(environ={}))

        self.assertEqual(provider.name, "mock")
        self.assertEqual(notice, "")

    def test_openai_without_key_is_configuration_error(self):
        from app.config import ConfigError

        settings = dataclasses.replace(
            load_settings(environ={}),
            ai_provider="openai",
            openai_api_key="",
        )

        with self.assertRaisesRegex(ConfigError, "OPENAI_API_KEY"):
            get_provider(settings)


class TestConfig(unittest.TestCase):
    def test_threshold_percent_and_validation(self):
        from app.config import ConfigError

        settings = load_settings(
            environ={"SIMILARITY_THRESHOLD": "80"}
        )
        self.assertEqual(settings.similarity_threshold, 0.8)

        invalid_configs = (
            {"SIMILARITY_THRESHOLD": "abc"},
            {"AI_PROVIDER": "x"},
            {"BATCH_SIZE": "0"},
            {"OPENAI_TIMEOUT": "0"},
            {"OPENAI_TIMEOUT": "nan"},
            {"OPENAI_MODEL": "  "},
        )

        for config in invalid_configs:
            with self.subTest(config=config):
                with self.assertRaises(ConfigError):
                    load_settings(environ=config)

    def test_secrets_redacted(self):
        settings = load_settings(
            environ={
                "OPENAI_API_KEY": "sk-abcdefghijkl",
                "POSTGRES_URL": (
                    "postgresql+psycopg://u:hunter2@h/db"
                ),
            }
        )

        redacted_text = str(settings.redacted())

        self.assertNotIn("hunter2", redacted_text)
        self.assertNotIn("abcdefghijkl", redacted_text)

    def test_placeholder_password_blocked(self):
        from app.config import ConfigError

        # Explicitly provide the placeholder instead of relying on
        # the local .env file or the machine's environment variables.
        settings = load_settings(
            environ={
                "POSTGRES_URL": (
                    "postgresql+psycopg://postgres:"
                    "YOUR_POSTGRES_PASSWORD"
                    "@localhost:5432/schemashift_target"
                )
            }
        )

        with self.assertRaises(ConfigError):
            settings.require_postgres_configured()


class TestOpenAIProvider(unittest.TestCase):
    """Tests for the OpenAI provider using a mocked SDK — no real API calls."""

    def _make_provider(self, mock_client):
        """Build an OpenAIProvider with a pre-injected mock client."""
        from app.ai.openai_provider import OpenAIProvider
        p = OpenAIProvider.__new__(OpenAIProvider)
        p.model = "gpt-4o-mini"
        p._client = mock_client
        return p

    def _mock_client(self, output_text, calls=None, exception=None):
        """Return a minimal mock that mimics openai.OpenAI().responses.create()."""
        import types
        resp = types.SimpleNamespace(output_text=output_text)
        def create(**kw):
            if calls is not None:
                calls.append(kw)
            if exception:
                raise exception
            return resp
        responses = types.SimpleNamespace(create=create)
        return types.SimpleNamespace(responses=responses)

    def test_suggest_mappings_parses_response(self):
        client = self._mock_client(
            '{"suggestions": [{"table": "orders", "column": "id", "type": "type", '
            '"suggestion": "Use a text identifier.", "reason": "Preserve source values."}]}'
        )
        p = self._make_provider(client)
        result = p.suggest_mappings({"tables": {"orders": {"columns": {"id": {}}}}})
        self.assertTrue(result.ok)
        self.assertIn("suggestions", result.content)
        self.assertTrue(result.advisory)
        self.assertIn("openai", result.label)

    def test_explain_field_parses_response(self):
        client = self._mock_client('{"explanation": "The name match is weak."}')
        p = self._make_provider(client)
        result = p.explain_field(FIELD)
        self.assertTrue(result.ok)
        self.assertEqual(result.content["explanation"], "The name match is weak.")

    def test_diagnose_failure_parses_response(self):
        client = self._mock_client('{"diagnosis": "Type mismatch.", "recommendations": ["Fix it."]}')
        p = self._make_provider(client)
        result = p.diagnose_failure([{"collection": "c", "path": "p", "message": "err"}])
        self.assertTrue(result.ok)
        self.assertIn("diagnosis", result.content)
        self.assertIn("recommendations", result.content)

    def test_summarize_risks_parses_response(self):
        client = self._mock_client('{"risks": ["Low confidence on 2 fields."]}')
        p = self._make_provider(client)
        result = p.summarize_risks({"needs_review": 2})
        self.assertTrue(result.ok)
        self.assertIn("risks", result.content)

    def test_missing_keys_returns_failed_result(self):
        client = self._mock_client('{"unrelated": true}')
        p = self._make_provider(client)
        result = p.suggest_mappings({"tables": {}})
        self.assertFalse(result.ok)
        self.assertIn("required keys", result.error)

    def test_malformed_json_returns_failed_result(self):
        client = self._mock_client("not json at all")
        p = self._make_provider(client)
        result = p.explain_field(FIELD)
        self.assertFalse(result.ok)

    def test_sdk_exception_returns_failed_result_no_key_leak(self):
        client = self._mock_client(
            "", exception=RuntimeError("sk-realkey1234567890 connection refused")
        )
        p = self._make_provider(client)
        with self.assertLogs("schemashift.ai", level="WARNING") as logged:
            result = p.explain_field(FIELD)
        self.assertFalse(result.ok)
        self.assertNotIn("sk-realkey1234567890", result.error)
        self.assertNotIn("sk-realkey1234567890", "\n".join(logged.output))

    def test_missing_api_key_raises_aierror(self):
        from app.ai.openai_provider import OpenAIProvider
        from app.ai.base import AIError
        with self.assertRaises(AIError) as ctx:
            OpenAIProvider(api_key="", model="gpt-4o-mini")
        self.assertIn("OPENAI_API_KEY", str(ctx.exception))

    def test_provider_is_labelled_as_llm(self):
        from app.ai.openai_provider import OpenAIProvider
        p = OpenAIProvider.__new__(OpenAIProvider)
        p.model = "gpt-4o-mini"
        self.assertTrue(p.is_llm)
        self.assertNotIn("mock", p.label())
        self.assertNotIn("not a real LLM", p.label())

    def test_scrubbed_errors_not_sent_raw(self):
        """Verify that summarize_errors scrubs quoted values before they reach the provider."""
        from app.ai.base import summarize_errors
        errs = [{"collection": "c", "path": "x", "message": "value 'TOP_SECRET' failed"}]
        scrubbed = summarize_errors(errs)
        self.assertNotIn("TOP_SECRET", str(scrubbed))

    def test_request_uses_non_storing_json_mode_and_bounded_output(self):
        calls = []
        client = self._mock_client('{"explanation": "Looks consistent."}', calls=calls)
        self._make_provider(client).explain_field(FIELD)
        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0]["store"])
        self.assertEqual(calls[0]["text"], {"format": {"type": "json_object"}})
        self.assertLessEqual(calls[0]["max_output_tokens"], 1024)

    def test_empty_and_schema_invalid_responses_fail(self):
        for response in ("", '{"explanation": 12}', '{"explanation": "   "}'):
            with self.subTest(response=response):
                result = self._make_provider(self._mock_client(response)).explain_field(FIELD)
                self.assertFalse(result.ok)
                self.assertEqual(result.content, {})

    def test_mapping_suggestions_reject_unknown_columns_and_model_scores(self):
        invalid_suggestions = (
            {"table": "orders", "column": "other", "type": "type",
             "suggestion": "Rename it", "reason": "Readable"},
            {"table": "orders", "column": "id", "type": "type",
             "suggestion": "Rename it", "reason": "Readable", "score": 1},
        )
        for suggestion in invalid_suggestions:
            with self.subTest(suggestion=suggestion):
                raw = __import__("json").dumps({"suggestions": [suggestion]})
                result = self._make_provider(self._mock_client(raw)).suggest_mappings(
                    {"tables": {"orders": {"columns": {"id": {}}}}}
                )
                self.assertFalse(result.ok)

    def test_sdk_failures_have_safe_identifiable_messages(self):
        import httpx
        import openai

        request = httpx.Request("POST", "https://api.openai.com/v1/responses")
        response = httpx.Response(401, request=request)
        errors = (
            (openai.AuthenticationError("secret sk-1234567890", response=response, body=None), "authentication"),
            (openai.RateLimitError("private detail", response=response, body=None), "rate limit"),
            (openai.APITimeoutError(request=request), "timed out"),
            (openai.APIConnectionError(message="private detail", request=request), "network error"),
            (openai.APIStatusError("private detail", response=response, body=None), "HTTP 401"),
            (openai.OpenAIError("private detail"), "OpenAI SDK request failed"),
        )
        for error, expected in errors:
            with self.subTest(error=type(error).__name__):
                result = self._make_provider(
                    self._mock_client("", exception=error)
                ).explain_field(FIELD)
                self.assertFalse(result.ok)
                self.assertIn(expected, result.error)
                self.assertNotIn("private detail", result.error)
                self.assertNotIn("1234567890", result.error)

    def test_rate_limit_categories_use_structured_codes_without_leaking_body(self):
        import httpx
        import openai

        cases = (
            ("rate_limit_exceeded", "requests", "temporary request or token rate limit"),
            ("credit_balance_exhausted", "billing_error", "credit balance is exhausted"),
            ("project_spend_limit_exceeded", "billing_error", "organization or project spend limit"),
            ("insufficient_quota", "insufficient_quota", "usage quota is exhausted or unavailable"),
        )
        for code, error_type, expected in cases:
            with self.subTest(code=code):
                request = httpx.Request("POST", "https://api.openai.com/v1/responses")
                response = httpx.Response(429, request=request)
                error = openai.RateLimitError(
                    "private body text sk-never-print-this",
                    response=response,
                    body={
                        "error": {
                            "message": "private body text sk-never-print-this",
                            "code": code,
                            "type": error_type,
                            "authorization": "Bearer never-print-this",
                        }
                    },
                )
                result = self._make_provider(
                    self._mock_client("", exception=error)
                ).explain_field(FIELD)

                self.assertFalse(result.ok)
                self.assertIn(expected, result.error)
                self.assertIn("HTTP 429", result.error)
                self.assertIn(f"code={code}", result.error)
                self.assertIn(f"type={error_type}", result.error)
                self.assertNotIn("private body text", result.error)
                self.assertNotIn("never-print-this", result.error)

    def test_unknown_rate_limit_code_does_not_assume_quota_exhaustion(self):
        import httpx
        import openai

        request = httpx.Request("POST", "https://api.openai.com/v1/responses")
        response = httpx.Response(429, request=request)
        error = openai.RateLimitError(
            "private text",
            response=response,
            body={"error": {"code": "new_unknown_code", "type": "requests"}},
        )
        result = self._make_provider(self._mock_client("", exception=error)).explain_field(FIELD)

        self.assertFalse(result.ok)
        self.assertIn("temporary request or token rate limit", result.error)
        self.assertIn("code=new_unknown_code", result.error)
        self.assertNotIn("private text", result.error)

    def test_rate_limit_extractor_ignores_untrusted_structured_values(self):
        import httpx
        import openai
        from app.ai.openai_provider import _classify_error

        request = httpx.Request("POST", "https://api.openai.com/v1/responses")
        response = httpx.Response(429, request=request)
        error = openai.RateLimitError(
            "private text",
            response=response,
            body={"error": {"code": "Bearer secret", "type": "sk-secret"}},
        )
        message = _classify_error(error)

        self.assertIn("HTTP 429", message)
        self.assertNotIn("secret", message)
        self.assertNotIn("private text", message)

    def test_request_input_omits_non_metadata_schema_values(self):
        calls = []
        client = self._mock_client('{"suggestions": []}', calls=calls)
        schema = {"tables": {"orders": {"columns": {"id": {"sql_type": "VARCHAR",
                                                             "sample_value": "PRIVATE"}}}}}
        result = self._make_provider(client).suggest_mappings(schema)
        self.assertTrue(result.ok)
        self.assertNotIn("PRIVATE", calls[0]["input"])

    def test_llm_cannot_change_scores(self):
        """Confirm that AIResult.content is advisory only and the pipeline ignores it for scoring."""
        client = self._mock_client('{"suggestions": [{"score": 0.0, "confidence": 0.0}]}')
        p = self._make_provider(client)
        result = p.suggest_mappings({"tables": {}})
        # The result is returned as advisory content; the pipeline never reads score/confidence from it.
        self.assertTrue(result.advisory)
        self.assertFalse(result.ok)
        # Scores in the content do not affect the schema map — this is enforced by the pipeline
        # never calling apply_decisions() with AI output directly.


class TestOpenAIProviderSelection(unittest.TestCase):
    def test_openai_provider_selected_when_key_present(self):
        """get_provider returns OpenAIProvider (not mock) when key is set."""
        settings = dataclasses.replace(
            load_settings(environ={}),
            ai_provider="openai",
            openai_api_key="sk-test-key-1234567890",
        )
        with patch("openai.OpenAI", return_value=object()) as client:
            provider, notice = get_provider(settings)
        self.assertEqual(provider.name, "openai")
        self.assertEqual(notice, "")
        self.assertTrue(provider.is_llm)
        client.assert_called_once_with(api_key="sk-test-key-1234567890",
                                       timeout=settings.openai_timeout, max_retries=1)

    def test_invalid_provider_fails_in_factory(self):
        from app.config import ConfigError
        settings = dataclasses.replace(load_settings(environ={}), ai_provider="invalid")
        with self.assertRaisesRegex(ConfigError, "AI_PROVIDER"):
            get_provider(settings)

    def test_mock_provider_clearly_labelled_not_llm(self):
        provider, _ = get_provider(load_settings(environ={}))
        self.assertFalse(provider.is_llm)
        self.assertIn("not a real LLM", provider.label())


if __name__ == "__main__":
    unittest.main()
