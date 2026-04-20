import os
import psycopg2
import psycopg2.extras
from contextlib import contextmanager
from dotenv import load_dotenv

load_dotenv()


def get_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 5432)),
        dbname=os.getenv("DB_NAME", "finviz_analyzer"),
        user=os.getenv("DB_USER", "finviz"),
        password=os.getenv("DB_PASSWORD"),
    )


@contextmanager
def db_cursor(commit=True):
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_run(run_date) -> int:
    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO digest_runs (run_date, status)
            VALUES (%s, 'pending')
            ON CONFLICT (run_date) DO UPDATE SET status = 'pending', created_at = NOW()
            RETURNING id
            """,
            (run_date,),
        )
        return cur.fetchone()["id"]


def update_run(run_id: int, status: str, article_count: int = None, error: str = None):
    with db_cursor() as cur:
        cur.execute(
            """
            UPDATE digest_runs
            SET status = %s, article_count = %s, error_message = %s
            WHERE id = %s
            """,
            (status, article_count, error, run_id),
        )


def insert_article(run_id: int, url: str, headline: str, source: str, ticker: str,
                   full_text: str, text_truncated: bool, paywall: bool, crawl_error: str = None) -> int:
    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO articles
                (run_id, url, headline, source, ticker, full_text, text_truncated, paywall, crawl_error)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (run_id, url, headline, source, ticker, full_text, text_truncated, paywall, crawl_error),
        )
        return cur.fetchone()["id"]


def insert_article_analysis(article_id: int, run_id: int, score: int, upshot: str,
                             theme: str, asset_class: str, is_duplicate: bool,
                             duplicate_of_id: int = None, is_stale: bool = False,
                             stale_note: str = None):
    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO article_analysis
                (article_id, run_id, relevance_score, upshot, theme, asset_class,
                 is_duplicate, duplicate_of_id, is_stale, stale_note)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (article_id, run_id, score, upshot, theme, asset_class, is_duplicate,
             duplicate_of_id, is_stale, stale_note),
        )
        return cur.fetchone()["id"]


def insert_theme(run_id: int, name: str, summary: str, article_count: int,
                 convergence_signal: str, convergence_note: str):
    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO themes
                (run_id, name, summary, article_count, convergence_signal, convergence_note)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (run_id, name, summary, article_count, convergence_signal, convergence_note),
        )
        return cur.fetchone()["id"]


def insert_recommendation(run_id: int, title: str, recommendation: str, direction: str,
                           sector: str, confidence: str, rationale: str, supporting_theme: str,
                           portfolio_note: str = None):
    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO recommendations
                (run_id, title, recommendation, direction, sector, confidence, rationale, supporting_theme, portfolio_note)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (run_id, title, recommendation, direction, sector, confidence, rationale, supporting_theme, portfolio_note),
        )
        return cur.fetchone()["id"]


def insert_digest(run_id: int, html_body: str, sent_to: str, executive_summary: str):
    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO digests (run_id, html_body, sent_to, executive_summary, sent_at)
            VALUES (%s, %s, %s, %s, NOW())
            RETURNING id
            """,
            (run_id, html_body, sent_to, executive_summary),
        )
        return cur.fetchone()["id"]


def insert_watchlist_item(run_id: int, ticker: str, company: str, reason: str,
                           direction: str, urgency: str):
    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO watchlist (run_id, ticker, company, reason, direction, urgency)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (run_id, ticker, company, reason, direction, urgency),
        )
        return cur.fetchone()["id"]


def upsert_position(ticker: str, company: str, shares: float,
                    avg_cost: float, asset_type: str, notes: str):
    with db_cursor() as cur:
        cur.execute(
            """
            INSERT INTO portfolio_positions (ticker, company, shares, avg_cost, asset_type, notes, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (ticker) DO UPDATE SET
                company    = COALESCE(EXCLUDED.company, portfolio_positions.company),
                shares     = EXCLUDED.shares,
                avg_cost   = COALESCE(EXCLUDED.avg_cost, portfolio_positions.avg_cost),
                asset_type = COALESCE(EXCLUDED.asset_type, portfolio_positions.asset_type),
                notes      = COALESCE(EXCLUDED.notes, portfolio_positions.notes),
                updated_at = NOW()
            """,
            (ticker, company, shares, avg_cost, asset_type, notes),
        )


def get_portfolio() -> list:
    with db_cursor(commit=False) as cur:
        cur.execute(
            "SELECT ticker, company, shares, avg_cost, asset_type, notes, updated_at "
            "FROM portfolio_positions ORDER BY asset_type, ticker"
        )
        return cur.fetchall()


def get_position(ticker: str) -> dict:
    with db_cursor(commit=False) as cur:
        cur.execute(
            "SELECT ticker, company, shares, avg_cost, asset_type, notes "
            "FROM portfolio_positions WHERE ticker = %s",
            (ticker,),
        )
        return cur.fetchone()


def remove_position(ticker: str) -> bool:
    with db_cursor() as cur:
        cur.execute("DELETE FROM portfolio_positions WHERE ticker = %s RETURNING id", (ticker,))
        return cur.fetchone() is not None


def get_recent_headlines(days: int = 3) -> list:
    """Fetch headlines from the past N days for cross-day dedup context."""
    with db_cursor(commit=False) as cur:
        cur.execute(
            """
            SELECT a.headline, aa.theme, dr.run_date
            FROM articles a
            JOIN article_analysis aa ON aa.article_id = a.id
            JOIN digest_runs dr ON aa.run_id = dr.id
            WHERE dr.run_date >= CURRENT_DATE - INTERVAL '%s days'
              AND dr.run_date < CURRENT_DATE
              AND dr.status = 'success'
              AND aa.relevance_score >= 5
              AND aa.is_duplicate = FALSE
            ORDER BY dr.run_date DESC, aa.relevance_score DESC
            LIMIT 80
            """,
            (days,),
        )
        return cur.fetchall()


def get_recent_themes(days: int = 7) -> list:
    """Fetch themes from the past N days for trend context."""
    with db_cursor(commit=False) as cur:
        cur.execute(
            """
            SELECT t.name, t.convergence_signal, t.convergence_note, dr.run_date
            FROM themes t
            JOIN digest_runs dr ON t.run_id = dr.id
            WHERE dr.run_date >= CURRENT_DATE - INTERVAL '%s days'
              AND dr.status = 'success'
            ORDER BY dr.run_date DESC
            """,
            (days,),
        )
        return cur.fetchall()
