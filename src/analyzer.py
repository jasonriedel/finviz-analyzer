import json
import logging
import subprocess
import time

logger = logging.getLogger(__name__)

# ── Prompt: score a batch of articles ─────────────────────────────────────────
SCORING_PROMPT = """You are a senior financial analyst focused on equities and macro. NOT interested in crypto or options.

Priorities: 1) Tech sector, 2) Energy (esp. Iran/Middle East supply), 3) Macro (Fed/rates/inflation/GDP), 4) Other equities.

RECENT HEADLINES FROM THE PAST 3 DAYS (to detect stale stories):
{recent_headlines}

For each article in the list below, return a JSON array with one object per article:
- id: (integer, matches input)
- relevance_score: 0-10 (10=market-moving; 0=irrelevant/crypto/noise)
- upshot: 1-2 sentence insight for an equity/macro trader (empty string if score=0)
- theme: short label grouping related stories (e.g. "Fed Policy", "Tech AI Spend", "Iran Energy Risk")
- asset_class: tech | energy | macro | financials | healthcare | consumer | industrials | other
- tickers: list of ticker symbols mentioned or implied (e.g. ["NVDA", "AMD"]) — empty list if none
- is_duplicate: true/false (same story covered by another article in THIS batch)
- duplicate_of_id: integer id of the better article, or null
- is_stale: true/false — true if this is essentially the same story as a recent headline above with no new information
- stale_note: brief explanation if is_stale=true, else empty string

Return ONLY a valid JSON array. No markdown, no explanation.

Articles:
{articles_json}
"""

# ── Prompt: synthesize themes + recommendations + watchlist ───────────────────
SYNTHESIS_PROMPT = """You are a senior financial analyst. Based on today's scored article summaries, produce a market digest.

Today: {today}

RECENT THEMES (past 7 days for trend context):
{recent_themes}

USER'S CURRENT PORTFOLIO (live eTrade positions — you MUST factor these into every recommendation):
{portfolio_context}

When the portfolio is non-empty:
- Flag any existing position that today's macro signals argue for trimming or exiting
- Flag any existing position that is reinforced by today's themes (hold/add signal)
- Size new recommendations relative to portfolio context (avoid over-concentration)
- If portfolio is empty or unavailable, omit portfolio-specific commentary

SCORED ARTICLES (id, score, headline, upshot, theme, asset_class, tickers):
{scored_json}

Return ONLY valid JSON with this exact structure (no markdown):
{{
  "themes": [
    {{
      "name": "<theme>",
      "summary": "<2-3 sentence overview>",
      "article_count": <n>,
      "convergence_signal": "<strong|moderate|weak|none>",
      "convergence_note": "<what direction the articles point, or empty>"
    }}
  ],
  "recommendations": [
    {{
      "title": "<short actionable title>",
      "recommendation": "<specific recommendation>",
      "direction": "<bullish|bearish|neutral>",
      "sector": "<sector>",
      "confidence": "<high|medium|low>",
      "rationale": "<cite themes/articles, mention specific tickers where relevant>",
      "supporting_theme": "<theme name>",
      "portfolio_note": "<if user holds a related position: explicit hold/trim/add/exit signal with reason — empty string if not applicable>"
    }}
  ],
  "watchlist": [
    {{
      "ticker": "<TICKER>",
      "company": "<company name>",
      "reason": "<1-2 sentence reason to watch today>",
      "direction": "<bullish|bearish|watch>",
      "urgency": "<today|this_week>"
    }}
  ],
  "executive_summary": "<3-4 sentence market narrative for today>"
}}

Produce 4-8 watchlist items. Focus on specific tickers with clear catalysts from today's news.
Tickers from the tech and energy sectors are especially valued.
"""


def _format_recent_headlines(recent_headlines: list) -> str:
    if not recent_headlines:
        return "None available."
    lines = []
    for h in recent_headlines:
        lines.append(f"- [{h['run_date']}] {h['headline']} (theme: {h['theme'] or 'unknown'})")
    return "\n".join(lines)


def _format_portfolio(portfolio: list) -> str:
    if not portfolio:
        return "No positions on file."
    lines = []
    for p in portfolio:
        est = f"~${float(p['shares']) * float(p['avg_cost']):,.0f}" if p['avg_cost'] else "cost unknown"
        cost_str = f"avg cost ${p['avg_cost']:,.2f}" if p['avg_cost'] else "avg cost unknown"
        line = (
            f"- {p['ticker']}: {p['shares']:,.2f} shares | {cost_str} | {est} | "
            f"{p['asset_type'] or 'stock'}"
        )
        if p.get('company'):
            line += f" | {p['company']}"
        if p.get('notes'):
            line += f" | NOTE: {p['notes']}"
        lines.append(line)
    return "\n".join(lines)


def _format_recent_themes(recent_themes: list) -> str:
    if not recent_themes:
        return "No prior theme history available."
    lines = []
    for t in recent_themes:
        lines.append(
            f"- [{t['run_date']}] {t['name']} "
            f"(convergence: {t['convergence_signal']}): {t['convergence_note'] or ''}"
        )
    return "\n".join(lines)


def run_claude(prompt: str, timeout: int = 480) -> str:
    """Run claude -p and return stdout."""
    result = subprocess.run(
        ["claude", "-p", prompt],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude -p failed (exit {result.returncode}): {result.stderr[:500]}")
    return result.stdout.strip()


def _parse_json(raw: str) -> any:
    """Strip markdown fences if present, then parse JSON."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
        text = "\n".join(lines[1:end])
    return json.loads(text)


def _score_batch(batch: list[dict], recent_headlines_str: str) -> list[dict]:
    """Score a batch of articles with retry. Returns list of scored dicts."""
    articles_json = json.dumps(
        [{"id": a["id"], "headline": a["headline"], "source": a["source"],
          "ticker": a["ticker"], "full_text": a["full_text"]} for a in batch],
        indent=2
    )
    prompt = SCORING_PROMPT.format(
        recent_headlines=recent_headlines_str,
        articles_json=articles_json,
    )
    for attempt in range(2):
        try:
            raw = run_claude(prompt, timeout=480)
            return _parse_json(raw)
        except Exception as e:
            if attempt == 0:
                logger.warning(f"  Batch attempt 1 failed: {e}. Retrying in 5s...")
                time.sleep(5)
            else:
                raise


def analyze_articles(articles: list[dict], today: str,
                     recent_themes: list, recent_headlines: list,
                     portfolio: list = None) -> dict:
    """
    Two-pass analysis:
      Pass 1 — score articles in batches of 25 (with stale/duplicate detection)
      Pass 2 — synthesize themes, recommendations, and watchlist
    """
    analyzable = []
    for a in articles:
        if a.get("full_text"):
            analyzable.append(a)
        elif a.get("headline"):
            a = dict(a)
            a["full_text"] = "[PAYWALL — headline only]"
            analyzable.append(a)

    recent_headlines_str = _format_recent_headlines(recent_headlines)
    logger.info(f"Scoring {len(analyzable)} articles in batches of 25...")

    BATCH_SIZE = 25
    all_scored: list[dict] = []
    for i in range(0, len(analyzable), BATCH_SIZE):
        batch = analyzable[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (len(analyzable) + BATCH_SIZE - 1) // BATCH_SIZE
        logger.info(f"  Batch {batch_num}/{total_batches} ({len(batch)} articles)...")
        try:
            scored = _score_batch(batch, recent_headlines_str)
            all_scored.extend(scored)
        except Exception as e:
            logger.error(f"  Batch {batch_num} failed after retry: {e}. Skipping.")

    logger.info(f"Scoring complete. {len(all_scored)} articles scored.")

    # ── Pass 2: synthesize ─────────────────────────────────────────────────
    id_to_article = {a["id"]: a for a in analyzable}

    scored_summary = []
    for s in all_scored:
        art = id_to_article.get(s.get("id"), {})
        if s.get("relevance_score", 0) >= 3 and not s.get("is_stale") and not s.get("is_duplicate"):
            scored_summary.append({
                "id": s.get("id"),
                "score": s.get("relevance_score"),
                "headline": art.get("headline", ""),
                "upshot": s.get("upshot", ""),
                "theme": s.get("theme", ""),
                "asset_class": s.get("asset_class", "other"),
                "tickers": s.get("tickers", []),
            })

    scored_summary.sort(key=lambda x: x.get("score", 0), reverse=True)

    logger.info(f"Synthesizing from {len(scored_summary)} fresh relevant articles...")
    synthesis_prompt = SYNTHESIS_PROMPT.format(
        today=today,
        recent_themes=_format_recent_themes(recent_themes),
        portfolio_context=_format_portfolio(portfolio or []),
        scored_json=json.dumps(scored_summary, indent=2),
    )

    for attempt in range(2):
        try:
            raw_synthesis = run_claude(synthesis_prompt, timeout=300)
            synthesis = _parse_json(raw_synthesis)
            break
        except Exception as e:
            if attempt == 0:
                logger.warning(f"Synthesis attempt 1 failed: {e}. Retrying in 5s...")
                time.sleep(5)
            else:
                logger.error(f"Synthesis failed after retry: {e}")
                synthesis = {"themes": [], "recommendations": [], "watchlist": [], "executive_summary": ""}

    logger.info("Synthesis complete.")

    return {
        "articles": all_scored,
        "themes": synthesis.get("themes", []),
        "recommendations": synthesis.get("recommendations", []),
        "watchlist": synthesis.get("watchlist", []),
        "executive_summary": synthesis.get("executive_summary", ""),
    }
