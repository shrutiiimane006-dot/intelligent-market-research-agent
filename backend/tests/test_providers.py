import json
import urllib.error

import pytest

from app import providers
from app.providers import ProviderError


class FakeProviderError(Exception):
    def __init__(self, status_code):
        super().__init__("provider failure")
        self.status_code = status_code


class FakeTypes:
    class GenerateContentConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs


def test_tavily_missing_key_fails_without_fallback(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="not configured") as error:
        providers.tavily_search("market query", 5, "basic")
    assert error.value.code == "missing_tavily_key"


def test_tavily_settings_reject_invalid_values_and_clamp_limits(monkeypatch):
    monkeypatch.setenv("MAX_SEARCH_RESULTS", "not-a-number")
    with pytest.raises(ProviderError) as error:
        providers.tavily_settings()
    assert error.value.code == "invalid_search_config"
    monkeypatch.setenv("MAX_SEARCH_RESULTS", "500")
    monkeypatch.setenv("SEARCH_DEPTH", "advanced")
    assert providers.tavily_settings() == (20, "advanced")
    monkeypatch.setenv("SEARCH_DEPTH", "unsupported")
    with pytest.raises(ProviderError) as error:
        providers.tavily_settings()
    assert error.value.code == "invalid_search_config"


def test_tavily_http_failure_is_reported(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test-only")
    monkeypatch.setattr(providers.time, "sleep", lambda _seconds: None)
    calls = {"count": 0}

    def fail(*_args, **_kwargs):
        calls["count"] += 1
        raise urllib.error.HTTPError("https://api.tavily.com/search", 503, "unavailable", {}, None)

    monkeypatch.setattr(providers.urllib.request, "urlopen", fail)
    with pytest.raises(ProviderError) as error:
        providers.tavily_search("market query", 5, "basic")
    assert error.value.code == "tavily_unavailable"
    assert "503" in error.value.message
    assert calls["count"] == 3


def test_tavily_rate_limit_is_not_retried(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test-only")
    calls = {"count": 0}

    def fail(*_args, **_kwargs):
        calls["count"] += 1
        raise urllib.error.HTTPError("https://api.tavily.com/search", 429, "rate limited", {}, None)

    monkeypatch.setattr(providers.urllib.request, "urlopen", fail)
    with pytest.raises(ProviderError) as error:
        providers.tavily_search("market query", 5, "basic")
    assert error.value.code == "tavily_rate_limited"
    assert calls["count"] == 1


def test_tavily_invalid_credentials_are_reported_without_retry(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test-only-invalid")
    calls = {"count": 0}

    def fail(*_args, **_kwargs):
        calls["count"] += 1
        raise urllib.error.HTTPError("https://api.tavily.com/search", 401, "unauthorized", {}, None)

    monkeypatch.setattr(providers.urllib.request, "urlopen", fail)
    with pytest.raises(ProviderError) as error:
        providers.tavily_search("market query", 2, "basic")

    assert error.value.code == "tavily_auth_error"
    assert calls["count"] == 1


def test_tavily_rejects_malformed_json(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test-only")

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b"{"

    monkeypatch.setattr(providers.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
    with pytest.raises(ProviderError) as error:
        providers.tavily_search("market query", 5, "basic")
    assert error.value.code == "invalid_tavily_response"


def test_gemini_503_retries_a_bounded_number_of_times(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-only")
    monkeypatch.setenv("GEMINI_MODEL", "test-model")
    monkeypatch.setattr(providers.time, "sleep", lambda _seconds: None)
    calls = {"generate": 0}

    class Models:
        def get(self, **_kwargs):
            return type("Model", (), {"supported_actions": ["generateContent"]})()

        def generate_content(self, **_kwargs):
            calls["generate"] += 1
            if calls["generate"] < 3:
                raise FakeProviderError(503)
            return type("Response", (), {"text": json.dumps({"summary": "actual response"})})()

    client = type("Client", (), {"models": Models()})()
    monkeypatch.setattr(providers, "_gemini_client", lambda: (client, FakeTypes))
    result = providers.generate_json("system", "prompt")
    assert calls["generate"] == 3
    assert result == {"summary": "actual response"}


def test_gemini_504_is_classified_as_retryable_timeout():
    error = providers._gemini_error(FakeProviderError(504))

    assert error.code == "gemini_timeout"
    assert error.retryable is True


def test_gemini_model_lookup_404_is_reported_as_unavailable_model():
    error = providers._gemini_error(FakeProviderError(404))

    assert error.code == "gemini_model_not_found"
    assert error.retryable is False
    assert "update gemini_model" in error.message.lower()


def test_gemini_invalid_credentials_are_reported_without_retry(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-only-invalid")
    calls = {"generate": 0}

    class Models:
        def get(self, **_kwargs):
            return type("Model", (), {"supported_actions": ["generateContent"]})()

        def generate_content(self, **_kwargs):
            calls["generate"] += 1
            raise FakeProviderError(401)

    client = type("Client", (), {"models": Models()})()
    monkeypatch.setattr(providers, "_gemini_client", lambda: (client, FakeTypes))

    with pytest.raises(ProviderError) as error:
        providers.generate_json("system", "prompt")

    assert error.value.code == "gemini_auth_error"
    assert calls["generate"] == 1


def test_gemini_quota_error_is_not_retried(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-only")
    calls = {"generate": 0}

    class Models:
        def get(self, **_kwargs):
            return type("Model", (), {"supported_actions": ["generateContent"]})()

        def generate_content(self, **_kwargs):
            calls["generate"] += 1
            raise FakeProviderError(429)

    client = type("Client", (), {"models": Models()})()
    monkeypatch.setattr(providers, "_gemini_client", lambda: (client, FakeTypes))
    with pytest.raises(ProviderError) as error:
        providers.generate_json("system", "prompt")
    assert error.value.code == "gemini_quota_or_rate_limit"
    assert calls["generate"] == 1


def test_missing_gemini_key_is_explicit(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ProviderError) as error:
        providers.generate_json("system", "prompt")
    assert error.value.code == "missing_gemini_key"
    assert "not configured" in error.value.message


def test_numeric_market_data_requires_a_matching_source_quote():
    from app.agents import _validate_market_data

    sources = [{"id": 3, "snippet": "The global market value was 12.5 billion USD in 2025."}]
    data = {
        "market_data": [{
            "metric": "market value",
            "value": "12.5",
            "unit": "billion USD",
            "period": "2025",
            "geography": "global",
            "evidence_quote": "The global market value was 12.5 billion USD in 2025.",
            "source_ids": [3],
        }]
    }
    assert _validate_market_data(data, sources)[0]["value"] == "12.5"
    data["market_data"][0]["value"] = "14.2"
    with pytest.raises(ProviderError):
        _validate_market_data(data, sources)


def test_specialist_claims_require_existing_source_citations(monkeypatch):
    from app import agents

    monkeypatch.setattr(agents, "generate_json", lambda *_args: {
        "findings": [{"text": "A market claim", "source_ids": [], "verification_status": "Verified against available source"}],
        "summary": "Claim",
        "limitations": [],
    })
    with pytest.raises(ProviderError, match="citation"):
        agents.analyze_category("trends", "Question", [{
            "id": 1, "title": "Source", "url": "https://example.com", "snippet": "Evidence"
        }])


def test_fact_checker_discards_claims_not_from_specialists_and_requires_quotes(monkeypatch):
    from app import agents

    monkeypatch.setattr(agents, "generate_json", lambda *_args: {
        "reviewed_findings": [
            {
                "text": "The specialist claim",
                "source_ids": [1],
                "evidence_quotes": ["not in the source"],
                "verification_status": "Verified against available source",
            },
            {
                "text": "Invented reviewer claim",
                "source_ids": [1],
                "evidence_quotes": ["The source states evidence."],
                "verification_status": "Verified against available source",
            },
        ],
        "contradictions": [],
        "limitations": [],
    })
    result = agents.review_findings(
        "Question",
        {"market_trends": {"findings": [{"text": "The specialist claim", "source_ids": [1]}]}},
        [{"id": 1, "title": "Source", "url": "https://example.com", "snippet": "The source states evidence."}],
    )
    assert len(result["reviewed_findings"]) == 1
    assert result["reviewed_findings"][0]["verification_status"] == "Unable to verify"
    assert any("omitted" in limitation for limitation in result["limitations"])


def test_synthesis_omits_findings_that_were_not_fact_checked(monkeypatch):
    from app import agents

    monkeypatch.setattr(agents, "generate_json", lambda *_args: {
        "executive_summary": "Untrusted generated summary.",
        "findings": [{
            "text": "A fabricated claim",
            "source_ids": [1],
            "verification_status": "Verified against available source",
        }],
        "opportunities": [],
        "risks": [],
        "recommendations": [],
        "market_data": [],
        "limitations": [],
    })
    result = agents.synthesize_report(
        "Question",
        {},
        {},
        {"reviewed_findings": [{
            "text": "Fact-checked claim",
            "source_ids": [1],
            "verification_status": "Verified against available source",
        }]},
        [{"id": 1, "title": "Source", "url": "https://example.com", "snippet": "Evidence"}],
        [],
    )
    assert result["findings"] == []
    assert result["executive_summary"] == "Fact-checked claim"
    assert result["executive_summary_source_ids"] == [1]
    assert any("omitted" in limitation for limitation in result["limitations"])


def test_synthesis_recommendation_requires_a_citation(monkeypatch):
    from app import agents

    monkeypatch.setattr(agents, "generate_json", lambda *_args: {
        "executive_summary": "",
        "findings": [],
        "opportunities": [],
        "risks": [],
        "recommendations": [{"text": "Recommendation without evidence", "source_ids": []}],
        "market_data": [],
        "limitations": [],
    })
    with pytest.raises(ProviderError, match="citation"):
        agents.synthesize_report(
            "Question", {}, {}, {"reviewed_findings": []},
            [{"id": 1, "title": "Source", "url": "https://example.com", "snippet": "Evidence"}], [],
        )
