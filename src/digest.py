import os
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

DIRECTION_COLOR = {
    "bullish": "#16a34a",
    "bearish": "#dc2626",
    "neutral": "#6b7280",
}

CONFIDENCE_BG = {
    "high": "#dcfce7",
    "medium": "#fef9c3",
    "low": "#fee2e2",
}

CONVERGENCE_COLOR = {
    "strong": "#dc2626",
    "moderate": "#ea580c",
    "weak": "#ca8a04",
    "none": "#6b7280",
}

ASSET_CLASS_EMOJI = {
    "tech": "💻",
    "energy": "⚡",
    "macro": "🌐",
    "financials": "🏦",
    "healthcare": "🏥",
    "consumer": "🛍️",
    "industrials": "🏭",
    "other": "📰",
}


def _score_bar(score: int) -> str:
    filled = round(score / 2)
    empty = 5 - filled
    return "█" * filled + "░" * empty


def build_html(run_date: str, executive_summary: str, recommendations: list,
               themes: list, articles: list, watchlist: list = None) -> str:
    """Build the full HTML digest email."""
    watchlist = watchlist or []

    # Fresh articles (non-duplicate, non-stale, score >= 4)
    sorted_articles = sorted(
        [a for a in articles
         if not a.get("is_duplicate") and not a.get("is_stale") and a.get("relevance_score", 0) >= 4],
        key=lambda x: x.get("relevance_score", 0),
        reverse=True,
    )
    # Stale/continued coverage (non-duplicate, stale, score >= 4)
    stale_articles = sorted(
        [a for a in articles
         if not a.get("is_duplicate") and a.get("is_stale") and a.get("relevance_score", 0) >= 5],
        key=lambda x: x.get("relevance_score", 0),
        reverse=True,
    )

    # ── Recommendations HTML ───────────────────────────────────────────────
    rec_rows = ""
    for rec in recommendations:
        direction = rec.get("direction", "neutral").lower()
        confidence = rec.get("confidence", "medium").lower()
        dir_color = DIRECTION_COLOR.get(direction, "#6b7280")
        conf_bg = CONFIDENCE_BG.get(confidence, "#f3f4f6")
        dir_arrow = {"bullish": "▲", "bearish": "▼", "neutral": "◆"}.get(direction, "◆")
        rec_rows += f"""
        <tr>
          <td style="padding:12px 8px;border-bottom:1px solid #e5e7eb;vertical-align:top;">
            <div style="font-weight:700;font-size:14px;color:#111827;">{rec.get('title','')}</div>
            <div style="font-size:13px;color:#374151;margin-top:4px;">{rec.get('recommendation','')}</div>
            <div style="font-size:12px;color:#6b7280;margin-top:4px;font-style:italic;">{rec.get('rationale','')}</div>
          </td>
          <td style="padding:12px 8px;border-bottom:1px solid #e5e7eb;white-space:nowrap;vertical-align:top;text-align:center;">
            <span style="color:{dir_color};font-weight:700;font-size:14px;">{dir_arrow} {direction.upper()}</span>
          </td>
          <td style="padding:12px 8px;border-bottom:1px solid #e5e7eb;white-space:nowrap;vertical-align:top;text-align:center;">
            <span style="background:{conf_bg};padding:2px 8px;border-radius:9999px;font-size:12px;font-weight:600;">
              {confidence.upper()}
            </span>
          </td>
          <td style="padding:12px 8px;border-bottom:1px solid #e5e7eb;white-space:nowrap;vertical-align:top;color:#6b7280;font-size:12px;">
            {rec.get('sector','')}
          </td>
        </tr>
        """

    # ── Themes HTML ────────────────────────────────────────────────────────
    theme_cards = ""
    sorted_themes = sorted(themes, key=lambda t: (
        {"strong": 0, "moderate": 1, "weak": 2, "none": 3}.get(t.get("convergence_signal", "none"), 3),
        -t.get("article_count", 0)
    ))
    for theme in sorted_themes:
        signal = theme.get("convergence_signal", "none").lower()
        signal_color = CONVERGENCE_COLOR.get(signal, "#6b7280")
        signal_label = {"strong": "🔥 STRONG SIGNAL", "moderate": "⚠️ MODERATE", "weak": "〰 WEAK", "none": ""}.get(signal, "")
        convergence_note = theme.get("convergence_note", "")
        convergence_html = f'<div style="font-size:12px;color:{signal_color};margin-top:6px;font-weight:600;">{signal_label}</div>' if signal_label else ""
        note_html = f'<div style="font-size:12px;color:#6b7280;margin-top:4px;font-style:italic;">{convergence_note}</div>' if convergence_note else ""

        theme_cards += f"""
        <div style="background:#f9fafb;border:1px solid #e5e7eb;border-left:4px solid {signal_color};
                    border-radius:6px;padding:14px 16px;margin-bottom:12px;">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;">
            <div style="font-weight:700;font-size:14px;color:#111827;">{theme.get('name','')}</div>
            <div style="font-size:12px;color:#6b7280;white-space:nowrap;margin-left:12px;">
              {theme.get('article_count', 0)} articles
            </div>
          </div>
          <div style="font-size:13px;color:#374151;margin-top:6px;">{theme.get('summary','')}</div>
          {convergence_html}
          {note_html}
        </div>
        """

    # ── Watchlist HTML ─────────────────────────────────────────────────────
    watchlist_cards = ""
    wl_dir_color = {"bullish": "#16a34a", "bearish": "#dc2626", "watch": "#ca8a04"}
    wl_dir_bg = {"bullish": "#f0fdf4", "bearish": "#fef2f2", "watch": "#fffbeb"}
    wl_dir_arrow = {"bullish": "▲", "bearish": "▼", "watch": "◉"}
    for item in watchlist:
        direction = item.get("direction", "watch").lower()
        urgency = item.get("urgency", "this_week")
        dc = wl_dir_color.get(direction, "#6b7280")
        bg = wl_dir_bg.get(direction, "#f9fafb")
        arrow = wl_dir_arrow.get(direction, "◉")
        urgency_html = (
            '<span style="background:#fef3c7;color:#92400e;font-size:10px;font-weight:700;'
            'padding:1px 6px;border-radius:9999px;margin-left:6px;">TODAY</span>'
            if urgency == "today" else ""
        )
        watchlist_cards += f"""
        <td style="width:50%;padding:6px;vertical-align:top;">
          <div style="background:{bg};border:1px solid #e5e7eb;border-left:3px solid {dc};
                      border-radius:6px;padding:10px 12px;">
            <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
              <span style="font-size:15px;font-weight:900;color:{dc};">{arrow} {item.get('ticker','')}</span>
              <span style="font-size:11px;color:#6b7280;">{item.get('company','')}</span>
              {urgency_html}
            </div>
            <div style="font-size:12px;color:#374151;margin-top:5px;">{item.get('reason','')}</div>
          </div>
        </td>
        """

    # Pair watchlist items into rows of 2
    watchlist_rows = ""
    wl_items = watchlist
    for i in range(0, len(wl_items), 2):
        pair = wl_items[i:i+2]
        row_cells = ""
        for item in pair:
            direction = item.get("direction", "watch").lower()
            urgency = item.get("urgency", "this_week")
            dc = wl_dir_color.get(direction, "#6b7280")
            bg = wl_dir_bg.get(direction, "#f9fafb")
            arrow = wl_dir_arrow.get(direction, "◉")
            urgency_html = (
                '<span style="background:#fef3c7;color:#92400e;font-size:10px;font-weight:700;'
                'padding:1px 6px;border-radius:9999px;margin-left:6px;">TODAY</span>'
                if urgency == "today" else ""
            )
            row_cells += f"""
            <td style="width:50%;padding:5px;vertical-align:top;">
              <div style="background:{bg};border:1px solid #e5e7eb;border-left:3px solid {dc};
                          border-radius:6px;padding:10px 12px;">
                <div>
                  <span style="font-size:15px;font-weight:900;color:{dc};">{arrow} {item.get('ticker','')}</span>
                  <span style="font-size:11px;color:#6b7280;margin-left:6px;">{item.get('company','')}</span>
                  {urgency_html}
                </div>
                <div style="font-size:12px;color:#374151;margin-top:5px;">{item.get('reason','')}</div>
              </div>
            </td>"""
        if len(pair) == 1:
            row_cells += '<td style="width:50%;padding:5px;"></td>'
        watchlist_rows += f"<tr>{row_cells}</tr>"

    # ── Helper: build article rows ─────────────────────────────────────────
    def _article_rows_html(art_list: list, is_stale_section: bool = False) -> str:
        rows = ""
        for art in art_list:
            score = art.get("relevance_score", 0)
            asset = art.get("asset_class", "other").lower()
            emoji = ASSET_CLASS_EMOJI.get(asset, "📰")
            score_color = "#16a34a" if score >= 8 else "#ca8a04" if score >= 6 else "#374151"
            url = art.get("url", "#")
            headline = art.get("headline", "")
            source = art.get("source", "")
            upshot = art.get("upshot", "")
            theme = art.get("theme", "")
            stale_note = art.get("stale_note", "")

            # Ticker badges
            raw_tickers = art.get("tickers", [])
            if isinstance(raw_tickers, str):
                raw_tickers = [t.strip() for t in raw_tickers.split(",") if t.strip()]
            ticker_badges = "".join(
                f'<span style="background:#dbeafe;color:#1e40af;font-size:10px;font-weight:700;'
                f'padding:1px 5px;border-radius:3px;margin-right:3px;">${t}</span>'
                for t in raw_tickers[:4]
            )

            stale_note_html = (
                f'<div style="font-size:11px;color:#9ca3af;margin-top:3px;font-style:italic;">'
                f'↩ {stale_note}</div>'
            ) if stale_note and is_stale_section else ""

            rows += f"""
            <tr>
              <td style="padding:10px 8px;border-bottom:1px solid #e5e7eb;text-align:center;white-space:nowrap;">
                <span style="font-size:18px;font-weight:900;color:{score_color};">{score}</span>
                <div style="font-size:9px;color:#9ca3af;letter-spacing:1px;">{_score_bar(score)}</div>
              </td>
              <td style="padding:10px 8px;border-bottom:1px solid #e5e7eb;font-size:11px;color:#6b7280;text-align:center;">{emoji}</td>
              <td style="padding:10px 8px;border-bottom:1px solid #e5e7eb;vertical-align:top;">
                <div>
                  <a href="{url}" style="font-weight:600;font-size:13px;color:#1d4ed8;text-decoration:none;">{headline}</a>
                </div>
                <div style="margin-top:3px;">{ticker_badges}</div>
                <div style="font-size:11px;color:#9ca3af;margin-top:2px;">{source} · {theme}</div>
                <div style="font-size:12px;color:#374151;margin-top:4px;">{upshot}</div>
                {stale_note_html}
              </td>
            </tr>
            """
        return rows

    article_rows = _article_rows_html(sorted_articles)
    stale_rows = _article_rows_html(stale_articles, is_stale_section=True)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Finviz Market Digest — {run_date}</title>
</head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:24px 0;">
<tr><td align="center">
<table width="680" cellpadding="0" cellspacing="0" style="max-width:680px;width:100%;">

  <!-- Header -->
  <tr>
    <td style="background:#111827;padding:28px 32px;border-radius:8px 8px 0 0;">
      <div style="color:#f9fafb;font-size:22px;font-weight:800;letter-spacing:-0.5px;">
        📈 Finviz Market Digest
      </div>
      <div style="color:#9ca3af;font-size:13px;margin-top:4px;">{run_date} · Equities &amp; Macro Focus</div>
    </td>
  </tr>

  <!-- Executive Summary -->
  <tr>
    <td style="background:#1e3a5f;padding:20px 32px;">
      <div style="color:#93c5fd;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;">
        Executive Summary
      </div>
      <div style="color:#e0f2fe;font-size:14px;line-height:1.6;">{executive_summary}</div>
    </td>
  </tr>

  <!-- Main content -->
  <tr>
    <td style="background:#ffffff;padding:28px 32px;">

      <!-- Watchlist -->
      {"" if not watchlist else f'''
      <div style="font-size:16px;font-weight:800;color:#111827;margin-bottom:16px;padding-bottom:8px;border-bottom:2px solid #111827;">
        👁 Today&#39;s Watchlist
      </div>
      <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:32px;">
        {watchlist_rows}
      </table>'''}

      <!-- Trading Recommendations -->
      <div style="font-size:16px;font-weight:800;color:#111827;margin-bottom:16px;padding-bottom:8px;border-bottom:2px solid #111827;">
        🎯 Trading Recommendations
      </div>
      <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:32px;">
        <thead>
          <tr style="background:#f9fafb;">
            <th style="padding:8px;font-size:11px;color:#6b7280;text-align:left;font-weight:600;text-transform:uppercase;">Recommendation</th>
            <th style="padding:8px;font-size:11px;color:#6b7280;text-align:center;font-weight:600;text-transform:uppercase;">Direction</th>
            <th style="padding:8px;font-size:11px;color:#6b7280;text-align:center;font-weight:600;text-transform:uppercase;">Confidence</th>
            <th style="padding:8px;font-size:11px;color:#6b7280;text-align:left;font-weight:600;text-transform:uppercase;">Sector</th>
          </tr>
        </thead>
        <tbody>
          {rec_rows}
        </tbody>
      </table>

      <!-- Themes -->
      <div style="font-size:16px;font-weight:800;color:#111827;margin-bottom:16px;padding-bottom:8px;border-bottom:2px solid #111827;">
        🗂 Market Themes
      </div>
      <div style="margin-bottom:32px;">
        {theme_cards}
      </div>

      <!-- Ranked Articles -->
      <div style="font-size:16px;font-weight:800;color:#111827;margin-bottom:16px;padding-bottom:8px;border-bottom:2px solid #111827;">
        📰 Ranked Articles <span style="font-size:12px;font-weight:400;color:#6b7280;">(score ≥ 4, fresh only)</span>
      </div>
      <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:32px;">
        <thead>
          <tr style="background:#f9fafb;">
            <th style="padding:8px;font-size:11px;color:#6b7280;text-align:center;font-weight:600;width:50px;">SCORE</th>
            <th style="padding:8px;font-size:11px;color:#6b7280;text-align:center;font-weight:600;width:32px;"></th>
            <th style="padding:8px;font-size:11px;color:#6b7280;text-align:left;font-weight:600;">HEADLINE · UPSHOT</th>
          </tr>
        </thead>
        <tbody>
          {article_rows}
        </tbody>
      </table>

      <!-- Continued Coverage -->
      {"" if not stale_articles else f"""
      <div style="font-size:16px;font-weight:800;color:#6b7280;margin-bottom:16px;padding-bottom:8px;border-bottom:2px solid #e5e7eb;">
        🔁 Continued Coverage <span style="font-size:12px;font-weight:400;">(ongoing stories from prior days)</span>
      </div>
      <table width="100%" cellpadding="0" cellspacing="0">
        <thead>
          <tr style="background:#f9fafb;">
            <th style="padding:8px;font-size:11px;color:#9ca3af;text-align:center;font-weight:600;width:50px;">SCORE</th>
            <th style="padding:8px;font-size:11px;color:#9ca3af;text-align:center;font-weight:600;width:32px;"></th>
            <th style="padding:8px;font-size:11px;color:#9ca3af;text-align:left;font-weight:600;">HEADLINE · UPSHOT</th>
          </tr>
        </thead>
        <tbody>
          {stale_rows}
        </tbody>
      </table>
      """}

    </td>
  </tr>

  <!-- Footer -->
  <tr>
    <td style="background:#f9fafb;padding:16px 32px;border-radius:0 0 8px 8px;border-top:1px solid #e5e7eb;">
      <div style="font-size:11px;color:#9ca3af;text-align:center;">
        Generated by Finviz Analyzer · {run_date} ·
        Analysis powered by Claude · Source: <a href="https://finviz.com/news.ashx" style="color:#6b7280;">finviz.com/news.ashx</a>
      </div>
    </td>
  </tr>

</table>
</td></tr>
</table>
</body>
</html>"""

    return html


def send_email(html_body: str, run_date: str) -> None:
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", 587))
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    recipient = os.getenv("DIGEST_RECIPIENT")
    sender = os.getenv("DIGEST_FROM")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"📈 Market Digest — {run_date}"
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.ehlo()
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.sendmail(sender, [recipient], msg.as_string())

    logger.info(f"Digest email sent to {recipient}")
