import os
import time
import logging
from dataclasses import dataclass, field
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

load_dotenv()

logger = logging.getLogger(__name__)

MAX_ARTICLES = int(os.getenv("MAX_ARTICLES", 120))
TEXT_LIMIT = int(os.getenv("ARTICLE_TEXT_LIMIT", 1200))

PAYWALL_SIGNALS = [
    "subscribe to continue", "subscription required", "subscribers only",
    "sign in to read", "create a free account", "sign up to read",
    "this content is for subscribers", "become a member", "premium content",
    "article limit reached", "you've reached your limit",
]

SKIP_DOMAINS = ["youtube.com", "youtu.be", "twitter.com", "x.com", "reddit.com"]


@dataclass
class CrawledArticle:
    url: str
    headline: str
    source: str
    ticker: str
    full_text: str
    text_truncated: bool
    paywall: bool
    crawl_error: str = None


def _is_paywall(text: str, html: str) -> bool:
    text_lower = text.lower()
    html_lower = html.lower()
    return any(signal in text_lower or signal in html_lower for signal in PAYWALL_SIGNALS)


def _extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    # Remove boilerplate tags
    for tag in soup(["script", "style", "nav", "footer", "header", "aside",
                     "form", "noscript", "iframe", "figure", "figcaption"]):
        tag.decompose()
    # Prefer article/main body
    body = (
        soup.find("article")
        or soup.find("main")
        or soup.find(class_=lambda c: c and any(k in c for k in ["article", "story", "content", "post-body"]))
        or soup.find("body")
    )
    if body:
        return " ".join(body.get_text(separator=" ").split())
    return " ".join(soup.get_text(separator=" ").split())


def crawl_finviz_news(finviz_url: str) -> list[CrawledArticle]:
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )

        # ── Step 1: Load Finviz news page ──────────────────────────────────
        page = context.new_page()
        logger.info("Loading Finviz news page...")
        try:
            page.goto(finviz_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)
        except PlaywrightTimeout:
            logger.error("Timed out loading Finviz news page.")
            browser.close()
            return results

        html = page.content()
        soup = BeautifulSoup(html, "lxml")

        news_items = []
        # Finviz news table rows have class "nn" or news-related table cells
        # The news table has rows with a-tags inside td.nn-date / td.nn-text
        table = soup.find("table", id="news-table")
        if not table:
            # Fallback: find any table that looks like news
            table = soup.find("table", class_=lambda c: c and "news" in c.lower())

        if table:
            rows = table.find_all("tr")
            for row in rows:
                tds = row.find_all("td")
                link_td = None
                for td in tds:
                    a = td.find("a", href=True)
                    if a and a["href"].startswith("http"):
                        link_td = td
                        link_tag = a
                        break
                if not link_td:
                    continue

                url = link_tag["href"]
                headline = link_tag.get_text(strip=True)
                # Source is often in a span or separate element
                source_tag = link_td.find("span") or link_td.find("div", class_=lambda c: c and "source" in (c or ""))
                source = source_tag.get_text(strip=True) if source_tag else urlparse(url).netloc

                # Tickers: Finviz links them as /quote.ashx?t=TICKER
                tickers = []
                for a_tag in row.find_all("a", href=True):
                    href = a_tag["href"]
                    if "quote.ashx?t=" in href:
                        t = href.split("quote.ashx?t=")[-1].split("&")[0].upper()
                        if t and t not in tickers:
                            tickers.append(t)
                # Fallback: plain uppercase text in a non-link cell
                if not tickers:
                    for td in tds:
                        if td != link_td:
                            text = td.get_text(strip=True)
                            if text and len(text) <= 5 and text.isupper() and text.isalpha():
                                tickers.append(text)
                                break
                ticker = ",".join(tickers)

                if url and headline:
                    news_items.append({"url": url, "headline": headline,
                                       "source": source, "ticker": ticker})
        else:
            # Generic fallback: all external links with meaningful text
            for a in soup.find_all("a", href=True):
                href = a["href"]
                text = a.get_text(strip=True)
                if href.startswith("http") and "finviz.com" not in href and len(text) > 20:
                    news_items.append({"url": href, "headline": text,
                                       "source": urlparse(href).netloc, "ticker": ""})

        page.close()

        # Deduplicate URLs while preserving order
        seen_urls = set()
        unique_items = []
        for item in news_items:
            if item["url"] not in seen_urls:
                seen_urls.add(item["url"])
                unique_items.append(item)

        unique_items = unique_items[:MAX_ARTICLES]
        logger.info(f"Found {len(unique_items)} unique news links to crawl.")

        # ── Step 2: Crawl each article ─────────────────────────────────────
        article_page = context.new_page()

        for i, item in enumerate(unique_items):
            url = item["url"]
            domain = urlparse(url).netloc.lower()

            if any(skip in domain for skip in SKIP_DOMAINS):
                logger.info(f"[{i+1}/{len(unique_items)}] Skipping non-article domain: {domain}")
                continue

            logger.info(f"[{i+1}/{len(unique_items)}] Crawling: {url}")
            full_text = ""
            paywall = False
            truncated = False
            error = None

            try:
                article_page.goto(url, wait_until="domcontentloaded", timeout=20000)
                article_page.wait_for_timeout(1500)
                html_content = article_page.content()
                raw_text = _extract_text(html_content)

                if not raw_text or len(raw_text) < 100:
                    paywall = True
                    full_text = ""
                elif _is_paywall(raw_text, html_content):
                    paywall = True
                    # Grab whatever snippet is available before the wall
                    full_text = raw_text[:400]
                else:
                    if len(raw_text) > TEXT_LIMIT:
                        full_text = raw_text[:TEXT_LIMIT]
                        truncated = True
                    else:
                        full_text = raw_text

            except PlaywrightTimeout:
                error = "timeout"
                paywall = True
            except Exception as e:
                error = str(e)[:200]
                paywall = True

            results.append(CrawledArticle(
                url=url,
                headline=item["headline"],
                source=item["source"],
                ticker=item["ticker"],
                full_text=full_text,
                text_truncated=truncated,
                paywall=paywall,
                crawl_error=error,
            ))

            # Polite crawl delay
            time.sleep(0.8)

        article_page.close()
        browser.close()

    logger.info(f"Crawl complete. {len(results)} articles processed.")
    return results
