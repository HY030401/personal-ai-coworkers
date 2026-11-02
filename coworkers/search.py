"""Configurable search plus conservative page metadata verification."""
import ipaddress
import logging
import os
import re
import socket
from datetime import date, datetime, timedelta
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from coworkers.article_dates import excluded_page, publication_evidence

PRIMARY_DOMAINS = ["openai.com", "anthropic.com", "research.google", "blog.google",
                   "microsoft.com", "aws.amazon.com", "blog.langchain.com"]
EXPANDED_DOMAINS = PRIMARY_DOMAINS + ["ibm.com", "research.ibm.com", "spectrum.ieee.org",
                                     "ieee.org", "acm.org", "research.google.com", "deepmind.google"]
DOMAINS = EXPANDED_DOMAINS + ["google.com", "langchain.com"]


class SearchError(Exception):
    pass


logger = logging.getLogger(__name__)


def provider_message(response):
    """Extract error text only, never echoed inputs, headers or entire bodies."""
    if response is None:
        return "No provider response"
    try:
        payload = response.json()
    except (ValueError, requests.RequestException):
        return "Provider returned a non-JSON response; body omitted"

    def messages(value):
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [message for entry in value for message in messages(entry)]
        if isinstance(value, dict):
            # In particular, Pydantic validation errors can contain secret inputs.
            return [message for key in ("detail", "error", "message", "msg")
                    if key in value for message in messages(value[key])]
        return []

    text = " ".join(messages(payload)) or "No provider error message supplied"
    # Redact configured secrets even when a provider echoes one without a label.
    for name, value in os.environ.items():
        if value and re.search(r"key|token|secret|password|credential|authorization", name, re.I):
            text = text.replace(value, "[REDACTED]")
    text = re.sub(r"https?://\S+", "[URL omitted]", text, flags=re.I)
    text = re.sub(r"\bBearer\s+[^\s,;]+", "[REDACTED]", text, flags=re.I)
    text = re.sub(r"\b(?:tvly-|sk-)[A-Za-z0-9_-]+", "[REDACTED]", text)
    text = re.sub(r'''(?i)["']?(?:api[_-]?key|authorization|x-api-key|token|password|secret)["']?\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s,;]+)''',
                  "[credential omitted]", text)
    return " ".join(text.split())[:500]


def search_failure(provider, exc, response):
    """Return a useful UI category and write a safe terminal diagnostic."""
    status = response.status_code if response is not None else None
    if isinstance(exc, requests.Timeout):
        category, action = "timeout", "Retry after checking your connection."
    elif isinstance(exc, requests.ConnectionError):
        category, action = "connection", "Check network, proxy and TLS settings."
    elif isinstance(exc, requests.HTTPError):
        if status in (400, 422):
            category, action = "request validation", "The provider rejected search parameters; see the sanitized terminal message."
        elif status in (401, 403):
            category, action = "authentication/access", "Check the provider key and account permissions."
        elif status in (402, 432, 433):
            category, action = "quota/billing", "Check the provider account's credits and usage limits."
        elif status == 429:
            category, action = "rate limit", "Wait before retrying and check usage limits."
        elif (status is not None and status >= 500) or status == 434:
            category, action = "provider unavailable", "Retry later."
        else:
            category, action = "HTTP error", "See the sanitized terminal message."
    elif isinstance(exc, (KeyError, TypeError, ValueError)):
        category, action = "invalid response", "The provider response could not be decoded or had an unexpected structure."
    else:
        category, action = "request error", "Check the provider connection and terminal diagnostics."
    logger.warning("Search provider=%s status=%s error_type=%s category=%s message=%s",
                   provider, status if status is not None else "none", type(exc).__name__, category,
                   provider_message(response))
    http = f" (HTTP {status})" if status is not None else ""
    return SearchError(f"{provider.title()} search failed [{category}]{http}. {action}")


def canonical(url):
    parts = urlsplit(url)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", ""))


def authoritative(url):
    host = urlsplit(url).hostname or ""
    return any(host == d or host.endswith("." + d) for d in DOMAINS)


def public_url(url):
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.port not in (None, 443):
        raise ValueError("Only public HTTPS pages are supported")
    addresses = socket.getaddrinfo(parts.hostname, 443)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError("Non-public destination")


def fetch_page(url):
    # Validate each redirect; cap response size to avoid downloading huge files.
    for _ in range(5):
        public_url(url)
        with requests.get(url, timeout=15, allow_redirects=False, stream=True,
                          headers={"User-Agent": "PersonalAICoworkers/1.0"}) as response:
            if response.is_redirect:
                url = urljoin(url, response.headers["Location"])
                continue
            response.raise_for_status()
            if "text/html" not in response.headers.get("Content-Type", ""):
                raise ValueError("Page is not HTML")
            content = bytearray()
            for chunk in response.iter_content(16384):
                content.extend(chunk)
                if len(content) > 1_500_000:
                    raise ValueError("Page too large")
            return url, BeautifulSoup(bytes(content), "html.parser")
    raise ValueError("Too many redirects")


def verify(result, article_mode=False):
    item = dict(result, verified=False, verification="Page could not be verified", published=None)
    try:
        url, soup = fetch_page(result["url"])
        title = soup.find("meta", property="og:title")
        title = title.get("content") if title else (soup.title.get_text(" ", strip=True) if soup.title else None)
        if not title:
            raise ValueError("No page title")
        dates = publication_evidence(soup, result.get("published_date") or result.get("date"))
        item.update(dates)
        if article_mode:
            item["exclusion_reason"] = excluded_page(url, soup, title)
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()
        item.update(url=url, title=title, source=urlsplit(url).hostname,
                    content=soup.get_text(" ", strip=True)[:14000], verified=True,
                    checked_at=datetime.now().astimezone().isoformat(),
                    verification="Page fetched; title from page. " + dates["date_verification"])
    except (requests.RequestException, ValueError, OSError, KeyError) as exc:
        # Avoid logging provider credentials or request objects.
        item["verification"] = (f"Page verification failed: HTTP {exc.response.status_code}"
                                if isinstance(exc, requests.HTTPError) and exc.response is not None
                                else f"Page verification failed: {type(exc).__name__}")
        if isinstance(exc, ValueError):
            item["verification"] += f" ({exc})"
    return item


class SearchTool:
    def search(self, query, articles=False, domains=None):
        provider = os.getenv("SEARCH_PROVIDER", "tavily").lower()
        response = None
        try:
            if provider == "tavily":
                key = os.getenv("TAVILY_API_KEY")
                if not key:
                    raise SearchError("Set TAVILY_API_KEY in .env.")
                body = {"query": query, "max_results": 10, "search_depth": "advanced"}
                if articles:
                    # Tavily forbids time_range together with explicit date boundaries.
                    today = date.today()
                    body.update(start_date=(today - timedelta(days=30)).isoformat(),
                                end_date=today.isoformat(), include_domains=domains or PRIMARY_DOMAINS,
                                include_domains_mode="restrict", include_published_date=True,
                                filter_by_published_date=True, exclude_domains=["books.google.com"])
                response = requests.post("https://api.tavily.com/search", json=body,
                                         headers={"Authorization": f"Bearer {key}"}, timeout=30)
                response.raise_for_status()
                results = response.json()["results"]
            elif provider == "serper":
                key = os.getenv("SERPER_API_KEY")
                if not key:
                    raise SearchError("Set SERPER_API_KEY in .env.")
                response = requests.post("https://google.serper.dev/search", headers={"X-API-KEY": key},
                                         json={"q": query, "num": 10, **({"tbs": "qdr:m"} if articles else {})}, timeout=30)
                response.raise_for_status()
                results = [dict(r, url=r["link"], content=r.get("snippet", "")) for r in response.json().get("organic", [])]
            else:
                raise SearchError("SEARCH_PROVIDER must be tavily or serper.")
            items, seen = [], set()
            for result in results:
                url = result.get("url", "")
                if not url:
                    continue
                reason = None
                if articles:
                    reason = ("Duplicate search result" if canonical(url) in seen else
                              "Source is outside authoritative domains" if not authoritative(url) else
                              excluded_page(url, title=result.get("title", "")))
                elif canonical(url) in seen:
                    continue
                seen.add(canonical(url))
                if reason:
                    items.append(dict(result, published=None, verified=False, exclusion_reason=reason,
                                      verification="Skipped before page fetch: " + reason))
                else:
                    item = verify(result, article_mode=True) if articles else verify(result)
                    if articles:
                        item["discovery_query"] = query
                    items.append(item)
            return items
        except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
            # Do not log exc, traceback, request objects or authorization headers.
            error_response = exc.response if isinstance(exc, requests.RequestException) and exc.response is not None else response
            raise search_failure(provider, exc, error_response) from exc


def rejection_reasons(item, today=None):
    reasons = []
    if item.get("exclusion_reason"):
        reasons.append(item["exclusion_reason"])
    if not item.get("verified"):
        reasons.append(item.get("verification", "Page not verified"))
    if not authoritative(item.get("url", "")):
        reasons.append("Source is outside authoritative domains")
    # Report date failures independently of page-type or duplicate exclusions.
    if not recent(dict(item, verified=True, exclusion_reason=None), today):
        if not item.get("published"):
            reasons.append(item.get("date_verification", "No verified publication date"))
        else:
            reasons.append("Publication date is invalid, future, or outside the strict 30-day window")
    return list(dict.fromkeys(reasons))


def recent(item, today=None):
    if not item.get("verified") or not item.get("published") or item.get("exclusion_reason"):
        return False
    today = today or date.today()
    try:
        return today - timedelta(days=30) <= date.fromisoformat(item["published"]) <= today
    except (ValueError, TypeError):
        return False
