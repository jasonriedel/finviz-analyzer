#!/usr/bin/env python3
"""
Manage your eTrade portfolio positions.

Usage:
  python positions.py list
  python positions.py add TICKER SHARES [--avg-cost X] [--company NAME] [--type stock|etf|bond|cash] [--notes "..."]
  python positions.py update TICKER [--shares X] [--avg-cost X] [--notes "..."]
  python positions.py remove TICKER
  python positions.py import FILE.csv
"""
import argparse
import csv
import sys

from dotenv import load_dotenv

load_dotenv()

from src import database as db

# Known ETF tickers for auto-classification on CSV import
KNOWN_ETFS = {
    'JEPI', 'SGOV', 'SPY', 'QQQ', 'IWM', 'TLT', 'IEF', 'TBT', 'TBF',
    'GLD', 'SLV', 'XLE', 'XLF', 'XLK', 'XLV', 'XLI', 'XLU', 'XLP',
    'XLY', 'XLB', 'XLRE', 'VTI', 'VOO', 'VEA', 'VWO', 'BND', 'LQD',
    'HYG', 'AGG', 'DIA', 'MDY', 'IJR', 'EEM', 'EFA', 'USO', 'UNG',
    'ARKK', 'ARKG', 'ARKW', 'ARKF', 'ARKQ', 'CQQQ', 'FXI', 'MCHI',
}


def _fmt_currency(val):
    if val is None:
        return '—'
    return f'${val:,.2f}'


def _fmt_est_value(shares, avg_cost):
    if avg_cost is None:
        return '—'
    return f'${float(shares) * float(avg_cost):,.0f}'


def cmd_list(args):
    positions = db.get_portfolio()
    if not positions:
        print("No positions on file. Use 'add' or 'import' to get started.")
        return

    print()
    print(f"{'TICKER':<8}  {'SHARES':>10}  {'AVG COST':>10}  {'EST VALUE':>12}  {'TYPE':<8}  COMPANY")
    print('─' * 72)

    total_est = 0.0
    for p in positions:
        est_val = float(p['shares']) * float(p['avg_cost']) if p['avg_cost'] else None
        if est_val:
            total_est += est_val
        print(
            f"{p['ticker']:<8}  {p['shares']:>10,.2f}  "
            f"{_fmt_currency(p['avg_cost']):>10}  "
            f"{_fmt_est_value(p['shares'], p['avg_cost']):>12}  "
            f"{(p['asset_type'] or '—'):<8}  "
            f"{p['company'] or ''}"
        )
        if p.get('notes'):
            print(f"{'':>8}  NOTE: {p['notes']}")

    print('─' * 72)
    print(f"{'TOTAL EST VALUE':>44}  ${total_est:,.0f}")
    print(f"  Last updated: {positions[0]['updated_at'].strftime('%Y-%m-%d %H:%M') if positions else '—'}")
    print()


def cmd_add(args):
    db.upsert_position(
        ticker=args.ticker.upper(),
        company=args.company,
        shares=args.shares,
        avg_cost=args.avg_cost,
        asset_type=args.type,
        notes=args.notes,
    )
    est = f" (~${args.shares * args.avg_cost:,.0f} est value)" if args.avg_cost else ""
    print(f"Saved {args.ticker.upper()}: {args.shares:,.2f} shares @ {_fmt_currency(args.avg_cost)}{est}")


def cmd_update(args):
    existing = db.get_position(args.ticker.upper())
    if not existing:
        print(f"No position found for {args.ticker.upper()}. Use 'add' to create one.")
        sys.exit(1)
    db.upsert_position(
        ticker=args.ticker.upper(),
        company=args.company or existing['company'],
        shares=args.shares if args.shares is not None else existing['shares'],
        avg_cost=args.avg_cost if args.avg_cost is not None else existing['avg_cost'],
        asset_type=args.type or existing['asset_type'],
        notes=args.notes if args.notes is not None else existing['notes'],
    )
    print(f"Updated {args.ticker.upper()}.")


def cmd_remove(args):
    removed = db.remove_position(args.ticker.upper())
    if removed:
        print(f"Removed {args.ticker.upper()} from portfolio.")
    else:
        print(f"No position found for {args.ticker.upper()}.")


def cmd_import(args):
    """
    Import positions from an eTrade CSV export.

    eTrade CSV export path in eTrade:
      Accounts → Portfolio → Download (CSV)

    Expected columns (eTrade format):
      Symbol, Last Price, Change, Quantity, Price Paid, Total Value, Gain/Loss
    """
    try:
        with open(args.file, newline='', encoding='utf-8-sig') as f:
            lines = f.readlines()
    except FileNotFoundError:
        print(f"File not found: {args.file}")
        sys.exit(1)

    # Find the header row — must contain 'symbol' and one of qty/quantity
    header_idx = None
    for i, line in enumerate(lines):
        lower = line.lower()
        if 'symbol' in lower and ('qty' in lower or 'quantity' in lower):
            header_idx = i
            break

    if header_idx is None:
        print("Could not find a header row containing 'Symbol' and 'Qty'/'Quantity'.")
        print("Ensure this is an eTrade portfolio CSV export.")
        sys.exit(1)

    reader = csv.DictReader(lines[header_idx:])
    imported = 0
    skipped = 0

    for row in reader:
        row = {k.strip(): (v.strip() if v else '') for k, v in row.items() if k}

        ticker = (row.get('Symbol') or row.get('symbol') or '').replace('$', '').strip()
        if not ticker or ticker.lower() in ('symbol', 'total', 'account total', ''):
            skipped += 1
            continue
        # Skip subtotal / blank rows
        if ticker.startswith('--') or ticker.startswith('*'):
            skipped += 1
            continue

        qty_raw = (
            row.get('Qty') or row.get('Quantity') or row.get('qty') or
            row.get('quantity') or ''
        ).replace(',', '').strip()
        try:
            shares = float(qty_raw)
        except (ValueError, TypeError):
            print(f"  Skipping {ticker}: could not parse quantity '{qty_raw}'")
            skipped += 1
            continue

        cost_raw = (
            row.get('Price Paid') or row.get('Avg Cost') or
            row.get('Average Cost') or row.get('Cost Basis Per Share') or
            row.get('price paid') or ''
        ).replace('$', '').replace(',', '').strip()
        avg_cost = None
        try:
            if cost_raw:
                avg_cost = float(cost_raw)
        except (ValueError, TypeError):
            pass

        asset_type = 'etf' if ticker.upper() in KNOWN_ETFS else 'stock'

        db.upsert_position(
            ticker=ticker.upper(),
            company=None,
            shares=shares,
            avg_cost=avg_cost,
            asset_type=asset_type,
            notes=None,
        )
        est = f" (~${shares * avg_cost:,.0f})" if avg_cost else ""
        print(f"  {ticker.upper()}: {shares:,.2f} shares @ {_fmt_currency(avg_cost)}{est}")
        imported += 1

    print(f"\nImport complete: {imported} positions imported, {skipped} rows skipped.")
    if imported:
        print("Run 'python positions.py list' to verify.")


def main():
    parser = argparse.ArgumentParser(
        description='Manage eTrade portfolio positions for the Finviz digest.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python positions.py list
  python positions.py add JEPI 1234 --avg-cost 55.20 --type etf --company "JPMorgan Equity Premium Income"
  python positions.py add SGOV 985 --avg-cost 100.45 --type etf --company "iShares 0-3M Treasury Bill"
  python positions.py update JEPI --shares 1300 --avg-cost 55.80
  python positions.py remove JEPI
  python positions.py import ~/Downloads/PortfolioPositions.csv
        """,
    )
    sub = parser.add_subparsers(dest='command')

    sub.add_parser('list', help='Show all current positions')

    p_add = sub.add_parser('add', help='Add or overwrite a position')
    p_add.add_argument('ticker')
    p_add.add_argument('shares', type=float)
    p_add.add_argument('--avg-cost', type=float, dest='avg_cost')
    p_add.add_argument('--company', type=str, default=None)
    p_add.add_argument('--type', type=str, default='stock',
                       choices=['stock', 'etf', 'bond', 'cash', 'option'])
    p_add.add_argument('--notes', type=str, default=None)

    p_upd = sub.add_parser('update', help='Update an existing position')
    p_upd.add_argument('ticker')
    p_upd.add_argument('--shares', type=float, default=None)
    p_upd.add_argument('--avg-cost', type=float, dest='avg_cost', default=None)
    p_upd.add_argument('--company', type=str, default=None)
    p_upd.add_argument('--type', type=str, default=None,
                       choices=['stock', 'etf', 'bond', 'cash', 'option'])
    p_upd.add_argument('--notes', type=str, default=None)

    p_rm = sub.add_parser('remove', help='Remove a position')
    p_rm.add_argument('ticker')

    p_imp = sub.add_parser('import', help='Import from eTrade CSV export')
    p_imp.add_argument('file', help='Path to eTrade portfolio CSV file')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    {'list': cmd_list, 'add': cmd_add, 'update': cmd_update,
     'remove': cmd_remove, 'import': cmd_import}[args.command](args)


if __name__ == '__main__':
    main()
