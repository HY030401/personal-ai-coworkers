"""Evidence-based publication dates. Modified dates never establish freshness."""
import json
import re
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit


def parse_date(value, today=None):
    """Accept complete, unambiguous dates only; never infer a missing year."""
    text = str(value or "").strip()
    parsed = None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[T ].+)?", text):
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00")).date() if len(text) > 10 else date.fromisoformat(text)
        else:
            for fmt in ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y"):
                try:
                    parsed = datetime.strptime(text, fmt).date()
                    break
                except ValueError:
                    continue
            if parsed is None and re.search(r"\d{4}", text):
                parsed = parsedate_to_datetime(text).date()
    except (ValueError, TypeError, OverflowError):
        return None
    return parsed.isoformat() if parsed and parsed <= (today or date.today()) else None


def json_nodes(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from json_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from json_nodes(child)


def publication_evidence(soup, provider_date=None, today=None):
    evidence, modified = [], []

    def add(raw, source, updated=False):
        if raw:
            entry = {"raw": str(raw), "source": source, "date": parse_date(raw, today)}
            (modified if updated else evidence).append(entry)

    article_types = {"Article", "NewsArticle", "BlogPosting", "TechArticle", "Report", "ScholarlyArticle"}
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            for node in json_nodes(json.loads(script.string or script.get_text())):
                types = node.get("@type", [])
                types = [types] if isinstance(types, str) else types
                if not isinstance(types, list) or not article_types.intersection(types):
                    continue
                add(node.get("datePublished"), "JSON-LD datePublished")
                add(node.get("dateModified"), "JSON-LD dateModified", True)
        except (ValueError, TypeError):
            continue
    publication_keys = {"article:published_time", "og:published_time", "datepublished", "date", "pubdate",
                        "publishdate", "publication_date", "dc.date.issued", "dcterms.issued", "citation_publication_date"}
    modified_keys = {"article:modified_time", "og:updated_time", "datemodified", "last-modified", "dc.date.modified"}
    for meta in soup.find_all("meta"):
        key = str(meta.get("property") or meta.get("name") or meta.get("itemprop") or "").lower()
        if key in publication_keys | modified_keys:
            add(meta.get("content"), f"meta {key}", key in modified_keys)
    # Only semantically labeled time elements qualify. An arbitrary time could be an event date.
    for element in soup.find_all("time"):
        semantic_label = " ".join([str(element.get("itemprop", "")), " ".join(element.get("class", []))])
        parent_text = element.parent.get_text(" ", strip=True)
        # Prefer element semantics so a nearby 'updated' label cannot hide datePublished.
        label = semantic_label if re.search(r"datePublished|published|posted|pubdate|updated|modified", semantic_label, re.I) else parent_text[:250]
        if len(parent_text) > 250 and label != semantic_label:
            label = str(element.previous_sibling or "")[-80:]
        if re.search(r"updated|modified", label, re.I):
            add(element.get("datetime") or element.get_text(strip=True), "HTML time updated", True)
        elif re.search(r"datePublished|published|posted|pubdate", label, re.I):
            add(element.get("datetime") or element.get_text(strip=True), "HTML time publication")
    # Visible text must explicitly say published/posted, not simply contain a date.
    root = soup.find("article") or soup.find("main") or soup.body or soup
    text = root.get_text(" ", strip=True)
    date_pattern = r"(\d{4}-\d{2}-\d{2}|[A-Za-z]+ \d{1,2}, \d{4}|\d{1,2} [A-Za-z]+ \d{4})"
    for match in re.finditer(r"\b(Published|Posted|Updated|Modified)(?:\s+on)?\s*[:·-]?\s*" + date_pattern, text, re.I):
        add(match.group(2), "visible " + match.group(1).lower(), match.group(1).lower() in {"updated", "modified"})

    dates = {e["date"] for e in evidence if e["date"]}
    provider = {"raw": provider_date, "date": parse_date(provider_date, today), "status": "not supplied"}
    if provider_date:
        provider["status"] = "invalid or future date" if not provider["date"] else "unconfirmed: no page publication date"
    published = next(iter(dates)) if len(dates) == 1 else None
    if len(dates) > 1:
        reason = "Conflicting page publication dates; original publication is uncertain"
    elif published:
        reason = "Publication date confirmed by page evidence"
        if provider["date"]:
            provider["status"] = "matches page" if provider["date"] == published else "conflicts with page; page publication date used"
    else:
        reason = "No valid page publication date; provider-only and updated dates cannot establish original publication"
    return dict(published=published, publication_evidence=evidence, modified_evidence=modified,
                updated=next((e["date"] for e in modified if e["date"]), None), provider_date=provider,
                date_verification=reason)


def excluded_page(url, soup=None, title=""):
    """Exclude non-article destinations before dates can make them look fresh."""
    parts = urlsplit(url)
    host, path = (parts.hostname or "").lower(), parts.path.lower().rstrip("/")
    if host.startswith("books.google.") or "/books" in path.split("?")[0]:
        return "Google Books / book catalog is not a technical article"
    if re.search(r"/(events?|conferences?|webinars?|calendar)(/|$)", path):
        return "Event, conference or webinar page"
    if host.startswith(("docs.", "learn.")) or re.search(r"/(docs?|documentation|reference)(/|$)", path):
        return "Documentation or reference page"
    if re.search(r"/(careers|jobs|privacy|terms|legal|support|contact)(/|$)", path):
        return "Non-article utility, policy or career page"
    if path in {"", "/blog", "/blogs", "/news", "/research", "/publications", "/articles", "/insights"}:
        return "Generic landing or article index page"
    if re.search(r"/(blog|blogs)/(tags?|category|categories)(/|$)", path) or path in {"/think/topics", "/research/blog", "/en-us/research/blog"}:
        return "Generic topic or blog index page"
    if re.search(r"\b(event|webinar|conference)\b", title, re.I) and not re.search(r"\b(recap|report|lessons|highlights)\b", title, re.I):
        return "Event announcement rather than technical article"
    if soup:
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                nodes = list(json_nodes(json.loads(script.string or script.get_text())))
                types = {t for n in nodes for t in ([n.get("@type")] if isinstance(n.get("@type"), str) else n.get("@type", []) or []) if isinstance(t, str)}
                if types.intersection({"Event", "Book", "Course"}) and not types.intersection({"Article", "NewsArticle", "BlogPosting", "TechArticle", "ScholarlyArticle"}):
                    return "Structured metadata identifies an event, book or course"
            except (ValueError, TypeError):
                continue
    return None
