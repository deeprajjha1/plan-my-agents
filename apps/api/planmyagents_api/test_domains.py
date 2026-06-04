"""Reserved-test-domain helpers shared by mock + baseline adapters.

Both the real Firecrawl ``web_scraping`` baseline (see
``planmyagents_api.benchmark.baselines.firecrawl``) and the mock
``MockFirecrawlScraper`` (see ``planmyagents_api.agents.mock``) need
to agree on what counts as a *test-mode* URL — otherwise the
benchmark grader scores mock and real runs differently for an
identical ``test_mode`` expectation in the YAML cases.

This module exists so that agreement is enforced by one shared
function instead of by two parallel copies. Putting the helper at the
``planmyagents_api`` top level (not under ``benchmark.baselines/``)
also satisfies the firewall audit
(``scripts/audit_baseline_firewall.py``): the mock never has to
import from ``benchmark.baselines.*`` and the routing modules stay
clean.

The set of reserved suffixes is grounded in two RFCs:

* RFC-2606 — reserves ``example.com``, ``example.org``,
  ``example.net`` for use in documentation and examples.
* RFC-6761 — reserves the ``.test``, ``.invalid``, ``.localhost``
  TLDs as non-routable on the public DNS.

A URL whose host matches (or ends with) any reserved suffix is
guaranteed safe to issue requests against in tests: it never reaches
a real third-party server, so the wrapper exercises its full code
path with zero side effects.
"""

from __future__ import annotations

import urllib.parse

RESERVED_TEST_DOMAIN_SUFFIXES: tuple[str, ...] = (
    "example.com",
    "example.org",
    "example.net",
    ".test",
    ".invalid",
    ".localhost",
    "localhost",
)


def is_reserved_test_url(url: str) -> bool:
    """Return ``True`` when ``url``'s host matches a reserved-test
    suffix per RFC-2606 / RFC-6761.

    Empty / unparseable URLs return ``False`` — a URL must be
    affirmatively safe to be classified as test-mode; the default is
    "not test" so a buggy input doesn't accidentally suppress real
    safety checks downstream.
    """

    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    for suffix in RESERVED_TEST_DOMAIN_SUFFIXES:
        if host == suffix or host.endswith("." + suffix.lstrip(".")):
            return True
    return False
