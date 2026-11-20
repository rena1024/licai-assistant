"""按北京时间冻结尝试价格；跨时段仍按发起时估算，预留采用两档上界。"""
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import Field
from services.model_catalog import Price
from services.llm_types import LLMError

TZ = timezone(timedelta(hours=8))


class PriceSnapshot(Price):
    tariff: Literal['fixed', 'peak', 'off_peak'] = 'fixed'
    priced_at: int | None = Field(default=None, ge=0)
    pricing_basis: Literal['request_start'] = 'request_start'
    calendar_version: str | None = None


def snapshot(model, *, now=None):
    if not model.time_pricing:
        return model.price.model_dump(mode='json')
    import chinese_calendar as calendar
    moment = now or datetime.now(TZ)
    if moment.tzinfo is None:
        raise ValueError('选价时刻必须包含时区')
    moment = moment.astimezone(TZ)
    day = moment.date()
    # 不用 is_workday 判断周末：调休周末按厂商规则也享受空闲价格。
    try:
        holiday = calendar.is_holiday(day)
    except NotImplementedError:
        raise LLMError('pricing_calendar_unavailable', '分时计价日历不覆盖当前年份，请更新节假日日历后重试') from None
    minute = moment.hour * 60 + moment.minute
    peak = day.weekday() < 5 and not holiday and (540 <= minute < 720 or 840 <= minute < 1080)
    prices = model.price.model_dump(mode='json')
    if not peak:
        prices.update(model.time_pricing.off_peak.model_dump(mode='json'))
    return PriceSnapshot(**prices, tariff='peak' if peak else 'off_peak',
                         priced_at=int(moment.timestamp()), calendar_version=calendar.__version__).model_dump(mode='json')


def reservation_price(model):
    if not model.time_pricing:
        return model.price
    maximum = {name: max(getattr(model.price, name), getattr(model.time_pricing.off_peak, name))
               for name in ('input', 'output', 'cache_read')}
    return model.price.model_copy(update=maximum)
