import asyncio
from datetime import datetime, timezone
from services import market_data as md


def test_primary_failure_uses_independent_provider(monkeypatch):
    from services import quote_sources as qs
    md._cache.clear()
    qs._last.clear()
    calls = []
    def sina(codes):
        calls.append('sina')
        raise RuntimeError('unavailable')
    def tencent(codes):
        calls.append('tencent')
        return {c: {'price': 10, 'stock_name': c, 'market': 'A', 'currency': 'CNY',
                    'source_at': '2026-10-19T02:00:00+00:00'} for c in codes}
    monkeypatch.setattr(md, '_fetch_sina_quotes', sina)
    monkeypatch.setattr(qs, 'fetch_tencent', tencent)
    q = asyncio.run(md.get_realtime_quotes(['600000']))['600000']
    assert calls == ['sina', 'tencent']
    assert q['source_id'] == 'tencent' and q['price'] == 10
    md._cache.clear()
    monkeypatch.setattr(qs, 'fetch_tencent', lambda codes: {})
    old = asyncio.run(md.get_realtime_quotes(['600000', '000001']))
    assert old['600000']['price'] == 10 and old['600000']['quality'] == 'stale'
    assert old['600000']['source_at'] == q['source_at']
    assert old['000001']['price'] is None and old['000001']['quality'] == 'missing'


def test_tencent_symbols_units_and_timestamp():
    from services.quote_sources import parse_tencent, tencent_symbol
    assert [tencent_symbol(c) for c in ['600000','000001','HK.00700','US.AAPL']] == ['sh600000','sz000001','hk00700','usAAPL']
    fields = [''] * 50
    for index, value in {1:'浦发', 3:'10', 4:'9', 5:'9.5', 6:'200', 30:'20261019100000', 32:'11.11', 33:'11', 34:'9', 37:'1.2', 38:'220'}.items():
        fields[index] = value
    q = parse_tencent('600000', '~'.join(fields))
    assert q['volume'] == 20000 and q['amount'] == 2200000
    assert datetime.fromisoformat(q['source_at']).astimezone(timezone.utc).hour == 2
