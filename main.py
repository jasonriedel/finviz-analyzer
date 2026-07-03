#!/usr/bin/env python3
"""
Finviz Market Digest — main orchestrator.
Crawl → Analyze → Store → Email
"""
import json
import logging
import os
import sys
from datetime import date

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(os.path.dirname(__file__), "logs", "finviz.log"), mode="a"),
    ],
)
logger = logging.getLogger("main")

from src.crawler import crawl_finviz_news
from src.analyzer import analyze_articles
from src.digest import build_html, send_email
from src import database as db
def main():
    today = date.today()
    today_str = today.strftime("%B %d, %Y")
    logger.info(f"=== Finviz Digest Run: {today_str} ===")

    finviz_url = os.getenv("FINVIZ_NEWS_URL", "https://finviz.com/news.ashx")

    # ── 1. Create DB run record ────────────────────────────────────────────
    run_id = db.create_run(today)
    logger.info(f"DB run ID: {run_id}")

    try:
        # ── 2. Crawl ───────────────────────────────────────────────────────
        crawled = crawl_finviz_news(finviz_url)
        if not crawled:
            raise RuntimeError("No articles crawled from Finviz.")

        # ── 3. Persist raw articles ────────────────────────────────────────
        article_id_map = {}  # index in crawled list -> db article id
        articles_for_analysis = []

        for i, art in enumerate(crawled):
            db_id = db.insert_article(
                run_id=run_id,
                url=art.url,
                headline=art.headline,
                source=art.source,
                ticker=art.ticker,
                full_text=art.full_text,
                text_truncated=art.text_truncated,
                paywall=art.paywall,
                crawl_error=art.crawl_error,
            )
            article_id_map[i] = db_id

            # Include in analysis if we have any content
            if art.full_text or art.headline:
                articles_for_analysis.append({
                    "id": i,
                    "db_id": db_id,
                    "headline": art.headline or "",
                    "source": art.source or "",
                    "ticker": art.ticker or "",
                    "url": art.url,
                    "full_text": art.full_text or "",
                    "paywall": art.paywall,
                })

        logger.info(f"Stored {len(crawled)} articles. {len(articles_for_analysis)} eligible for analysis.")

        # ── 4. Fetch context from DB ───────────────────────────────────────
        recent_themes = db.get_recent_themes(days=7)
        recent_headlines = db.get_recent_headlines(days=3)

        logger.info(
            f"Context: {len(recent_themes)} recent themes, {len(recent_headlines)} recent headlines."
        )

        # ── 5. Run Claude analysis ─────────────────────────────────────────
        analysis = analyze_articles(articles_for_analysis, today_str, recent_themes, recent_headlines)

        # ── 6. Persist analysis ────────────────────────────────────────────
        # Map from analysis article id -> db analysis id (for duplicate linking)
        analysis_id_map = {}

        for art_result in analysis.get("articles", []):
            idx = art_result.get("id")
            db_article_id = article_id_map.get(idx)
            if db_article_id is None:
                continue

            # Resolve duplicate_of_id to a db analysis id
            dup_of = art_result.get("duplicate_of_id")
            dup_db_id = analysis_id_map.get(dup_of) if dup_of is not None else None

            db_analysis_id = db.insert_article_analysis(
                article_id=db_article_id,
                run_id=run_id,
                score=art_result.get("relevance_score", 0),
                upshot=art_result.get("upshot", ""),
                theme=art_result.get("theme", ""),
                asset_class=art_result.get("asset_class", "other"),
                is_duplicate=art_result.get("is_duplicate", False),
                duplicate_of_id=dup_db_id,
                is_stale=art_result.get("is_stale", False),
                stale_note=art_result.get("stale_note", ""),
            )
            analysis_id_map[idx] = db_analysis_id

        for theme in analysis.get("themes", []):
            db.insert_theme(
                run_id=run_id,
                name=theme.get("name", ""),
                summary=theme.get("summary", ""),
                article_count=theme.get("article_count", 0),
                convergence_signal=theme.get("convergence_signal", "none"),
                convergence_note=theme.get("convergence_note", ""),
            )

        for rec in analysis.get("recommendations", []):
            db.insert_recommendation(
                run_id=run_id,
                title=rec.get("title", ""),
                recommendation=rec.get("recommendation", ""),
                direction=rec.get("direction", "neutral"),
                sector=rec.get("sector", ""),
                confidence=rec.get("confidence", "medium"),
                rationale=rec.get("rationale", ""),
                supporting_theme=rec.get("supporting_theme", ""),
            )

        for item in analysis.get("watchlist", []):
            db.insert_watchlist_item(
                run_id=run_id,
                ticker=item.get("ticker", ""),
                company=item.get("company", ""),
                reason=item.get("reason", ""),
                direction=item.get("direction", "watch"),
                urgency=item.get("urgency", "this_week"),
            )

        # ── 7. Build enriched article list for email (merge crawl + analysis) ──
        analysis_by_idx = {a["id"]: a for a in analysis.get("articles", [])}
        email_articles = []
        for i, art in enumerate(articles_for_analysis):
            result = analysis_by_idx.get(i, {})
            email_articles.append({
                "url": art["url"],
                "headline": art["headline"],
                "source": art["source"],
                "relevance_score": result.get("relevance_score", 0),
                "upshot": result.get("upshot", ""),
                "theme": result.get("theme", ""),
                "asset_class": result.get("asset_class", "other"),
                "tickers": result.get("tickers", []),
                "is_duplicate": result.get("is_duplicate", False),
                "is_stale": result.get("is_stale", False),
                "stale_note": result.get("stale_note", ""),
            })

        # ── 8. Build and send HTML digest ──────────────────────────────────
        executive_summary = analysis.get("executive_summary", "")

        # Abort email if analysis is completely empty (e.g. all claude -p calls failed)
        has_content = (
            executive_summary
            or analysis.get("recommendations")
            or analysis.get("themes")
            or analysis.get("watchlist")
        )
        if not has_content:
            logger.warning("Analysis produced no content — skipping email to avoid sending an empty digest.")
            db.update_run(run_id, status="empty", article_count=len(crawled))
            return

        html_body = build_html(
            run_date=today_str,
            executive_summary=executive_summary,
            recommendations=analysis.get("recommendations", []),
            themes=analysis.get("themes", []),
            articles=email_articles,
            watchlist=analysis.get("watchlist", []),
        )

        send_email(html_body, today_str)

        # ── 9. Persist digest ──────────────────────────────────────────────
        db.insert_digest(
            run_id=run_id,
            html_body=html_body,
            sent_to=os.getenv("DIGEST_RECIPIENT"),
            executive_summary=executive_summary,
        )

        db.update_run(run_id, status="success", article_count=len(crawled))
        logger.info("=== Run complete ===")

    except Exception as e:
        logger.exception(f"Run failed: {e}")
        db.update_run(run_id, status="failed", error=str(e)[:500])
        sys.exit(1)
if __name__ == "__main__":
    main()
