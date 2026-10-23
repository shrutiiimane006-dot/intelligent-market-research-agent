import json
import os
import random
import time
import urllib.error
import urllib.request


class ProviderError(Exception):
    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def tavily_settings():
    try:
        max_results = max(1, min(int(os.environ.get("MAX_SEARCH_RESULTS", "8")), 20))
    except ValueError as exc:
        raise ProviderError("invalid_search_config", "The configured search result limit is invalid.") from exc
    search_depth = os.environ.get("SEARCH_DEPTH", "basic").strip().lower()
    if search_depth not in {"basic", "advanced"}:
        raise ProviderError("invalid_search_config", "Search depth must be basic or advanced.")
    return max_results, search_depth


def tavily_search(query, max_results, search_depth):
    api_key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not api_key:
        raise ProviderError("missing_tavily_key", "Live web search is not configured.")
    payload = json.dumps({
        "api_key": api_key,
        "query": query,
        "search_depth": search_depth,
        "max_results": max_results,
        "include_answer": False,
    }).encode("utf-8")
    request = urllib.request.Request(
        "https://api.tavily.com/search",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                data = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise ProviderError("tavily_rate_limited", "Web search rate limit reached.") from exc
            if exc.code in {401, 403}:
                raise ProviderError("tavily_auth_error", "Web search authentication or permission failed.") from exc
            retryable = exc.code in {500, 502, 503, 504}
            error = ProviderError(
                "tavily_unavailable" if retryable else "tavily_error",
                f"Web search returned HTTP {exc.code}.",
                retryable,
            )
            if not retryable or attempt == 2:
                raise error from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            error = ProviderError("tavily_unavailable", "Web search could not be reached.", True)
            if attempt == 2:
                raise error from exc
        except json.JSONDecodeError as exc:
            raise ProviderError("invalid_tavily_response", "Web search returned malformed JSON.") from exc
        time.sleep((0.5 * (2 ** attempt)) + random.uniform(0, 0.25))

    if not isinstance(data, dict):
        raise ProviderError("invalid_tavily_response", "Web search returned an unexpected response.")
    results = data.get("results")
    if not isinstance(results, list):
        raise ProviderError("invalid_tavily_response", "Web search returned an unexpected response.")
    return results


def _gemini_client():
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise ProviderError("missing_gemini_key", "AI analysis is not configured.")
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise ProviderError("gemini_sdk_missing", "The Gemini SDK is not installed.") from exc
    return genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=45000)), types


def verify_gemini_model():
    model = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash").strip()
    if not model:
        raise ProviderError("invalid_gemini_model", "Configure a non-empty Gemini model identifier.")
    client, _types = _gemini_client()
    try:
        model_info = client.models.get(model=model)
    except Exception as exc:
        raise _gemini_error(exc) from exc
    actions = getattr(model_info, "supported_actions", None) or []
    if not any("generatecontent" in str(action).lower() for action in actions):
        raise ProviderError("unsupported_gemini_model", "The configured Gemini model does not support text generation.")
    return model


def generate_json(system_instruction, prompt):
    client, types = _gemini_client()
    model = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash").strip()
    if not model:
        raise ProviderError("invalid_gemini_model", "Configure a non-empty Gemini model identifier.")
    for attempt in range(3):
        try:
            model_info = client.models.get(model=model)
            actions = getattr(model_info, "supported_actions", None) or []
            if not any("generatecontent" in str(action).lower() for action in actions):
                raise ProviderError("unsupported_gemini_model", "The configured Gemini model does not support text generation.")
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    temperature=0.2,
                ),
            )
            text = getattr(response, "text", None)
            if not isinstance(text, str) or not text.strip():
                raise ProviderError("empty_gemini_response", "AI analysis returned an empty response.")
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ProviderError("invalid_gemini_response", "AI analysis returned malformed structured data.") from exc
            if not isinstance(data, dict):
                raise ProviderError("invalid_gemini_response", "AI analysis returned an unexpected response shape.")
            return data
        except ProviderError:
            raise
        except Exception as exc:
            error = _gemini_error(exc)
            if not error.retryable or attempt == 2:
                raise error from exc
            time.sleep((0.5 * (2 ** attempt)) + random.uniform(0, 0.25))
    raise ProviderError("gemini_unavailable", "AI analysis could not be completed.")


def _gemini_error(exc):
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if status == 429:
        return ProviderError("gemini_quota_or_rate_limit", "AI provider quota or rate limit was reached.")
    if status == 503:
        return ProviderError("gemini_unavailable", "AI provider is temporarily unavailable.", True)
    if status == 504:
        return ProviderError("gemini_timeout", "AI provider request timed out.", True)
    if status in {401, 403}:
        return ProviderError("gemini_auth_error", "AI provider authentication or permission failed.")
    if status == 404:
        return ProviderError(
            "gemini_model_not_found",
            "The configured AI model was not found or is no longer available; update GEMINI_MODEL to a model supported for this API key.",
        )
    if status == 400:
        return ProviderError("gemini_invalid_request", "The AI provider rejected the request.")
    if isinstance(exc, TimeoutError):
        return ProviderError("gemini_timeout", "AI provider request timed out.", True)
    return ProviderError("gemini_error", "AI analysis failed because the provider could not complete the request.", True)
