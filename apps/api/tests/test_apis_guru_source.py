"""Tests for `ApisGuruSource`. Network is mocked; fixtures mirror the real
APIs.guru `/v2/list.json` shape observed on 2026-05-11."""

from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from planmyagents_api.discovery.sources import apis_guru as ag  # noqa: E402

from tests._capability_index_fixtures import extended_capability_index  # noqa: E402

# See _capability_index_fixtures.py for rationale: the shipped registry
# has only 5 slugs and these fixtures (Stripe payments, Scrapinghub)
# need extra capability slugs to classify under the new embedding-based
# inference. Without this, the source-mechanics tests would degrade to
# noise about registry composition rather than source behaviour.
_TEST_EXTRA_CAPS = ["payment_authorization"]

LIST_PAYLOAD = {
    "stripe.com": {
        "added": "2020-01-01",
        "preferred": "2020-08-27",
        "versions": {
            "2020-08-27": {
                "info": {
                    "title": "Stripe Payments API",
                    "description": "Charge a card. Authorize a payment. Refund.",
                    "x-providerName": "stripe.com",
                    "contact": {"url": "https://stripe.com"},
                },
                "swaggerUrl": "https://api.apis.guru/v2/specs/stripe.com/2020-08-27/openapi.json",
                "openapiVer": "3.0.0",
            },
            "2019-12-03": {
                "info": {
                    "title": "Stripe Payments API",
                    "description": "Older version.",
                },
                "swaggerUrl": "https://example/old.json",
            },
        },
    },
    "scrapinghub.com": {
        "added": "2018-05-12",
        "preferred": "1.0",
        "versions": {
            "1.0": {
                "info": {
                    "title": "Scrapinghub API",
                    "description": "Scrape and crawl websites at scale.",
                    "x-providerName": "scrapinghub.com",
                },
                "swaggerUrl": "https://api.apis.guru/v2/specs/scrapinghub.com/1.0/openapi.json",
            }
        },
    },
    "irrelevant.example": {
        "preferred": "1.0",
        "versions": {
            "1.0": {
                "info": {
                    "title": "Some Random API",
                    "description": "Does something completely unrelated to our taxonomy.",
                }
            }
        },
    },
    "malformed.example": {
        # Missing `versions` entirely — should not crash.
        "preferred": "1.0",
    },
}


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._buf = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def __enter__(self) -> io.BytesIO:
        return self._buf

    def __exit__(self, *_: object) -> None:
        self._buf.close()


def _fake_urlopen(payload: dict):
    def fn(req, timeout=None):  # noqa: ARG001
        return _FakeResponse(payload)

    return fn


class ApisGuruSourceTests(unittest.TestCase):
    def test_emits_candidates_only_for_capability_matches(self) -> None:
        source = ag.ApisGuruSource()
        with extended_capability_index(_TEST_EXTRA_CAPS), patch.object(
            ag.request, "urlopen", _fake_urlopen(LIST_PAYLOAD)
        ):
            candidates = source.search(capabilities=set(), task_description="")

        ids = sorted(c.id for c in candidates)
        self.assertIn("stripe-com", ids)
        self.assertIn("scrapinghub-com", ids)
        # `irrelevant.example` and `malformed.example` are dropped —
        # neither has text similar enough to any registry capability
        # (and the second has no versions).
        self.assertNotIn("irrelevant-example", ids)
        self.assertNotIn("malformed-example", ids)

    def test_picks_preferred_version_for_openapi_url(self) -> None:
        source = ag.ApisGuruSource()
        with extended_capability_index(_TEST_EXTRA_CAPS), patch.object(
            ag.request, "urlopen", _fake_urlopen(LIST_PAYLOAD)
        ):
            candidates = source.search(capabilities=set(), task_description="")
        stripe = next(c for c in candidates if c.id == "stripe-com")
        self.assertEqual(
            stripe.openapi_url,
            "https://api.apis.guru/v2/specs/stripe.com/2020-08-27/openapi.json",
        )

    def test_capability_filter_applied(self) -> None:
        source = ag.ApisGuruSource()
        with extended_capability_index(_TEST_EXTRA_CAPS), patch.object(
            ag.request, "urlopen", _fake_urlopen(LIST_PAYLOAD)
        ):
            candidates = source.search(
                capabilities={"web_scraping"}, task_description=""
            )
        ids = [c.id for c in candidates]
        self.assertIn("scrapinghub-com", ids)
        self.assertNotIn("stripe-com", ids)

    def test_max_candidates_bounds_emission(self) -> None:
        source = ag.ApisGuruSource(max_candidates=1)
        with extended_capability_index(_TEST_EXTRA_CAPS), patch.object(
            ag.request, "urlopen", _fake_urlopen(LIST_PAYLOAD)
        ):
            candidates = source.search(capabilities=set(), task_description="")
        self.assertEqual(len(candidates), 1)

    def test_provider_type_is_api_provider(self) -> None:
        source = ag.ApisGuruSource()
        with patch.object(ag.request, "urlopen", _fake_urlopen(LIST_PAYLOAD)):
            candidates = source.search(capabilities=set(), task_description="")
        for c in candidates:
            self.assertEqual(c.provider_type, "api_provider")
            self.assertEqual(c.source, "apis_guru")

    def test_handles_network_error_silently(self) -> None:
        source = ag.ApisGuruSource()

        def boom(req, timeout=None):  # noqa: ARG001
            raise OSError("dns failure")

        with patch.object(ag.request, "urlopen", boom):
            candidates = source.search(capabilities=set(), task_description="")
        self.assertEqual(candidates, [])


if __name__ == "__main__":
    unittest.main()
