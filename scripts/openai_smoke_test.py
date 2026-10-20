"""Explicitly opt-in, one-request check of the configured OpenAI Responses API."""
from __future__ import annotations

import os
import sys

from app.ai import AIError, get_provider
from app.config import ConfigError, load_settings


def main() -> int:
    if os.environ.get("SCHEMASHIFT_OPENAI_SMOKE") != "1":
        print("FAIL: set SCHEMASHIFT_OPENAI_SMOKE=1 to authorize one live OpenAI request")
        return 2

    try:
        settings = load_settings()
        if settings.ai_provider != "openai":
            print("FAIL: set AI_PROVIDER=openai in .env or the current PowerShell session")
            return 2
        provider, _ = get_provider(settings)
    except ConfigError as exc:
        print(f"FAIL: {exc}")
        return 1
    except AIError as exc:
        print(f"FAIL: {exc}")
        return 1
    except Exception as exc:
        print(f"FAIL: OpenAI provider setup failed ({type(exc).__name__})")
        return 1

    result = provider.summarize_risks({"needs_review": 0, "unmatched": 0, "overall_percent": 100})
    risks = result.content.get("risks") if result.ok else None
    if result.ok and isinstance(risks, list) and all(isinstance(item, str) for item in risks):
        print(f"PASS: one valid JSON response received from {settings.openai_model}")
        return 0

    print(f"FAIL: {result.error or 'provider returned an invalid smoke-test response'}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
