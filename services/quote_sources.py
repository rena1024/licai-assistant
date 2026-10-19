"""独立报价供应商及统一时效合同；获取时间不能替代源时间。"""
from __future__ import annotations
import asyncio
import math
import re
from datetime import datetime, timezone, timedelta, time as clock_time
from zoneinfo import ZoneInfo

_last: dict[str, dict] = {}


def tencent_symbol(code):
    from services.market_data import split_stock_code, _sina_symbol
    market, symbol = split_stock_code(code)
    if market == 'HK':
        return 'hk' + symbol
    if market == 'US':
        return 'us' + symbol.upper().replace('.', '.')
    return _sina_symbol(symbol)


def parse_tencent(code, payload):
    from services.market_data import split_stock_code
    market, _ = split_stock_code(code)
    f = payload.split('~')
    if len(f) < 39:
        return None
    try:
        price, previous = float(f[3]), float(f[4])
        if not math.isfinite(price) or price <= 0:
            return None
        # A 股以手、万元报价；港股/美股成交口径尚未实测，不猜测单位。
        at = None
        if re.fullmatch(r'\d{14}', f[30]):
            at = datetime.strptime(f[30], '%Y%m%d%H%M%S').replace(
                tzinfo=ZoneInfo('America/New_York' if market == 'US' else 'Asia/Shanghai')).isoformat()
        return {'stock_code': code, 'stock_name': f[1], 'market': market,
                'currency': {'A':'CNY','HK':'HKD','US':'USD'}[market],
                'price': price, 'prev_close': previous, 'open': float(f[5] or 0),
                'high': float(f[33] or 0), 'low': float(f[34] or 0),
                'change_pct': float(f[32] or 0), 'turnover_rate': float(f[37] or 0),
                'volume': float(f[6] or 0) * 100 if market == 'A' else None,
                'amount': float(f[38] or 0) * 10000 if market == 'A' else None,
                'source_at': at, 'unit': 'share'}
    except (ValueError, IndexError, OverflowError):
        return None


def fetch_tencent(codes):
    from services.market_data import _requests
    symbols = {tencent_symbol(c): c for c in codes}
    resp = _requests.get('https://qt.gtimg.cn/q=' + ','.join(symbols), timeout=8,
                         allow_redirects=False)
    resp.raise_for_status()
    resp.encoding = 'gbk'
    result = {}
    for symbol, payload in re.findall(r'v_([\w.]+)="([^"]*)"', resp.text[:2_000_000]):
        if symbol in symbols:
            quote = parse_tencent(symbols[symbol], payload)
            if quote:
                result[symbols[symbol]] = quote
    return result


def source_time(quote, now):
    from services.market_data import split_stock_code
    value = quote.get('source_at') or quote.get('quote_time')
    if value:
        try:
            at = datetime.fromisoformat(value)
            return at if at.tzinfo else at.replace(tzinfo=ZoneInfo('Asia/Shanghai'))
        except ValueError:
            return None
    value = quote.get('price_as_of')
    if value:
        try:
            at = datetime.strptime(value[:15] + ' ' + str(now.year), '%b %d %I:%M%p %Y').replace(tzinfo=ZoneInfo('America/New_York'))
            if (at - now).days > 30:
                at = at.replace(year=at.year - 1)
            return at
        except ValueError:
            return None
    return None



def last_session_boundary(market, now):
    from services.dca import _is_market_trading_day
    zone=ZoneInfo('America/New_York' if market=='US' else 'Asia/Shanghai')
    local=now.astimezone(zone)
    day=local.date()
    market_key='CN' if market=='A' else market
    opening=clock_time(9,30)
    if local.time()<opening:
        day-=timedelta(days=1)
    for _ in range(370):
        if _is_market_trading_day(day,market_key):
            break
        day-=timedelta(days=1)
    closing=clock_time(15) if market=='A' else clock_time(16)
    if day==local.date() and market in ('A','HK') and local.time()<clock_time(13):
        closing=clock_time(11,30) if market=='A' else clock_time(12)
    return datetime.combine(day,closing,tzinfo=zone)


def quality(quote, *, now=None, failed=False):
    from services.market_data import market_open
    now = now or datetime.now(timezone.utc)
    q = {key:None for key in ("price","change_pct","open","high","low","prev_close","volume","amount","amplitude","turnover_rate")}
    q.update(quote)
    at = source_time(q, now)
    q.update(source_at=at.isoformat() if at else None, unit=q.get('unit', 'share'))
    q.setdefault('fetched_at', now.isoformat())
    q['market_state'] = 'open' if market_open(q.get('market', 'A'), now) else 'closed'
    price = q.get('price')
    valid = isinstance(price, (float, int)) and math.isfinite(price) and price > 0
    q['quality'], q['stale_reason'] = 'fresh', None
    if not valid:
        q.update(price=None, quality='missing', stale_reason='no_price')
    elif failed:
        q.update(quality='stale', stale_reason='sources_unavailable')
    elif at is None:
        q.update(quality='unknown', stale_reason='source_time_unknown')
    elif at > now:
        q.update(quality='unknown', stale_reason='source_time_in_future')
    elif q['market_state'] == 'closed':
        boundary=last_session_boundary(q.get('market','A'),now)
        if (boundary-at).total_seconds()>300:
            q.update(quality='stale',stale_reason='older_than_last_session')
        else:
            q.update(quality='last_trade',stale_reason='market_closed')
    elif (now - at).total_seconds() > 300:
        q.update(quality='stale', stale_reason='source_delayed')
    elif (now - at).total_seconds() > 120:
        q.update(quality='delayed', stale_reason='source_delayed')
    return q


async def quotes(codes):
    from services import market_data as md, data_sources
    now = datetime.now(timezone.utc)
    result, providers = {}, {}
    policy = await data_sources.policy()
    async def fetch_sina(batch):
        found = {}
        a_codes = [c for c in batch if md.split_stock_code(c)[0] == 'A']
        if a_codes:
            try:
                found.update(await asyncio.to_thread(md._fetch_sina_quotes, a_codes))
            except Exception:
                pass
        async def overseas(code):
            market, symbol = md.split_stock_code(code)
            fn = md._fetch_hk_stock_quote if market == 'HK' else md._fetch_us_stock_quote
            return code, await asyncio.to_thread(fn, symbol)
        rows = await asyncio.gather(*(overseas(c) for c in batch if c not in a_codes), return_exceptions=True)
        for row in rows:
            if isinstance(row, tuple) and row[1]:
                found[row[0]] = row[1]
        return found
    for position in range(2):
        for provider in ('sina','tencent'):
            batch = [c for c in codes if c not in result and
                policy['quotes'][md.split_stock_code(c)[0]][position:position+1] == [provider]]
            if not batch:
                continue
            try:
                rows = await fetch_sina(batch) if provider == 'sina' else await asyncio.to_thread(fetch_tencent,batch)
            except Exception:
                rows = {}
            for c,q in rows.items():
                if c in batch and quality(q,now=now)['price'] is not None:
                    market = md.split_stock_code(c)[0]
                    result[c] = {**q, 'market':market, 'currency':md.code_currency(c)}
                    providers[c] = provider
            for market in {md.split_stock_code(c)[0] for c in batch}:
                successful = [q for c,q in result.items() if c in batch and md.split_stock_code(c)[0] == market]
                await data_sources.record('quote',provider,market,bool(successful),
                    source_at=source_time(successful[0],now).isoformat() if successful and source_time(successful[0],now) else None)
    out = {}
    for code in codes:
        if code in result:
            q = quality({**result[code], 'stock_code':code, 'source_id':providers[code]}, now=now)
            if q.get('currency', 'CNY') != 'CNY':
                fx = await asyncio.to_thread(md.get_fx_info, q['currency'])
                q.update(fx_rate=fx['rate'], fx_time=fx.get('time',''), fx_source=fx.get('source',''))
            else:
                q['fx_rate'] = 1.0
            _last[code] = dict(q)
            out[code] = q
        else:
            market, _ = md.split_stock_code(code)
            out[code] = quality(_last.get(code, {'stock_code':code, 'stock_name':'', 'market':market,
                'currency':md.code_currency(code), 'price':None, 'source_id':None}), now=now, failed=True)
    return out


def coverage(holdings):
    held = [h for h in holdings if h.get('shares', 0) > 0]
    covered = sum(h.get('market_value') is not None for h in held)
    stale = sum(h.get('quote_quality') in ('stale','delayed','unknown') for h in held)
    return {'held':len(held), 'covered':covered, 'stale':stale, 'missing':len(held)-covered,
            'coverage_pct':round(covered/len(held)*100, 1) if held else 100.0,
            'complete':covered == len(held) and stale == 0}


def can_graft_today(quote, day):
    """只有可信且所属交易日一致的报价才能写入当日日 K。"""
    if not quote or quote.get('quality') not in ('fresh','last_trade') or not quote.get('price'):
        return False
    try:
        at=datetime.fromisoformat(quote['source_at'])
        if at.tzinfo is None:
            return False
        return at.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()==day
    except (ValueError,KeyError,TypeError):
        return False
