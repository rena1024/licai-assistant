"""SQLite database setup and operations."""
from __future__ import annotations
import aiosqlite
from services.user_context import validate_user_id
from config import config
from services.runtime_paths import database_path, get_runtime_paths, LAYOUT_VERSION, RuntimePathError

# 观察池的默认分组。它是一个真实分组(watchlist_group 里有行), 不是"没有分组" ——
# 一只票可以同时在「自选」和「金矿」里。加进观察池时自动落这个组; 取消到一个组都不剩时
# 也退回它, 保证任何一只票至少能在某个分组视图里被看见。
DEFAULT_WATCH_GROUP = "自选"
ACCOUNT_SCHEMA_VERSION = "1"
PORTFOLIO_SCHEMA_VERSION = "1"
OWNED_ASK_TABLES = ('ask_session', 'ask_message', 'ask_run', 'tool_gap', 'prompt_rule')
OWNED_SETTINGS_TABLES = ('alert_config', 'custom_alerts', 'unwind_plans', 'unwind_tranches', 'watchlist', 'watchlist_group', 'cashflow_monthly', 'position_thesis', 'thesis_revision', 'brokers')
OWNED_PORTFOLIO_TABLES = ("external_asset_actions", "position_actions", "trade_log", "external_assets", "holdings", "portfolio_snapshots", "morning_briefings")
# 首次启动自动插入的模板不是用户配置；初始化保护据此识别被修改的无归属费率。
DEFAULT_BROKERS = (
    ("招商证券", 0.0001854, 5.0, 0.0001854, 5.0, 1),
    ("银河证券", 0.000086, 5.0, 0.00005, 0.1, 0),
    ("盈立证券", 0.0008, 0.0, 0.0008, 0.0, 0),
)


def resolve_action_time(action: dict) -> str:
    """成交时刻: trade_time(HH:MM, 用户手填)优先; 否则用 created_at(存的是 UTC)转北京
    时间取 HH:MM:SS。供分时图按真实时刻打 B/S 点。拿不到返回 ""。"""
    tt = (action.get("trade_time") or "").strip()
    if tt:
        return tt
    ca = action.get("created_at")
    if ca:
        try:
            from datetime import datetime, timedelta
            dt = datetime.fromisoformat(str(ca).replace("T", " ").split(".")[0])
            return (dt + timedelta(hours=8)).strftime("%H:%M:%S")
        except Exception:
            pass
    return ""

SCHEMA = """
CREATE TABLE IF NOT EXISTS user_settings (
 user_id INTEGER NOT NULL REFERENCES users(id),
 key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(user_id,key)
);
CREATE TABLE IF NOT EXISTS brokers (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id),
 name TEXT NOT NULL, stock_rate REAL NOT NULL, stock_min REAL NOT NULL,
 etf_rate REAL NOT NULL, etf_min REAL NOT NULL, is_default INTEGER NOT NULL DEFAULT 0,
 UNIQUE(user_id,name)
);
CREATE TABLE IF NOT EXISTS holdings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    stock_code TEXT NOT NULL,
    stock_name TEXT NOT NULL DEFAULT '',
    shares INTEGER NOT NULL,
    cost_price REAL NOT NULL,
    purchase_date TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, stock_code)
);

CREATE TABLE IF NOT EXISTS trade_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    stock_code TEXT NOT NULL,
    trade_type TEXT NOT NULL,
    price REAL NOT NULL,
    shares INTEGER NOT NULL,
    signal_source TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS alert_config (
    user_id INTEGER NOT NULL REFERENCES users(id),
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code TEXT NOT NULL,
    buy_zone_low REAL,
    buy_zone_high REAL,
    sell_zone_low REAL,
    sell_zone_high REAL,
    enabled INTEGER DEFAULT 1,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, stock_code)
);

CREATE TABLE IF NOT EXISTS kline_cache (
    stock_code TEXT NOT NULL,
    date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL,
    volume REAL DEFAULT 0,
    amount REAL DEFAULT 0,               -- 成交额(元); 副图切「成交额」用, 源缺则 0
    PRIMARY KEY (stock_code, date)
);

CREATE TABLE IF NOT EXISTS custom_alerts (
    user_id INTEGER NOT NULL REFERENCES users(id),
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    price REAL NOT NULL,
    message TEXT DEFAULT '',
    enabled INTEGER DEFAULT 1,
    triggered INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS app_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS unwind_plans (
    user_id INTEGER NOT NULL REFERENCES users(id),
    stock_code TEXT NOT NULL,
    total_budget REAL NOT NULL,
    used_budget REAL DEFAULT 0,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, stock_code)
);

CREATE TABLE IF NOT EXISTS unwind_tranches (
    user_id INTEGER NOT NULL REFERENCES users(id),
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code TEXT NOT NULL,
    idx INTEGER NOT NULL,
    trigger_price REAL NOT NULL,
    shares INTEGER NOT NULL,
    requires_health TEXT DEFAULT 'any',
    status TEXT DEFAULT 'pending',
    triggered_at TIMESTAMP,
    executed_at TIMESTAMP,
    executed_price REAL,
    sold_back_price REAL,
    sold_back_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS external_assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    asset_type TEXT NOT NULL,       -- FUND / CRYPTO / BOT
    code TEXT NOT NULL,             -- fund code / symbol / bot label
    name TEXT NOT NULL,
    platform TEXT,                  -- 支付宝 / 招商 / OKX / 币安 / etc
    cost_amount REAL NOT NULL,      -- total cost in CNY (投入本金)
    shares REAL,                    -- optional: units held (funds use this)
    manual_value REAL,              -- manual override for current value (bots)
    note TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, id)
);

CREATE TABLE IF NOT EXISTS position_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    stock_code TEXT NOT NULL,
    action_type TEXT NOT NULL,
    price REAL NOT NULL,
    shares INTEGER NOT NULL,
    tranche_id INTEGER,
    note TEXT DEFAULT '',
    trade_date TEXT,
    fee REAL,                            -- 手续费 (CNY) 覆盖; NULL = 用 estimate_trade_fee 自动算
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS morning_briefings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    stock_code TEXT NOT NULL,
    briefing_date TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, stock_code, briefing_date)
);

-- 每日组合市值快照(收盘后记一次): 机器人/加密等无价史资产的真实市值序列,
-- 净值曲线用它替代成本基线(快照攒得越久, 这部分曲线越真)
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    user_id INTEGER NOT NULL REFERENCES users(id),
    snap_date TEXT NOT NULL,          -- YYYY-MM-DD
    total_value REAL NOT NULL,
    by_asset TEXT,                       -- JSON {"EXT:<id>"|"A:<code>": value}
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(user_id, snap_date)
);

-- 自选观察池(在跟踪但未必持有的票)。分组不在这张表上, 见 watchlist_group。
CREATE TABLE IF NOT EXISTS watchlist (
    user_id INTEGER NOT NULL REFERENCES users(id),
    stock_code TEXT NOT NULL,
    stock_name TEXT,
    added_at TEXT,                       -- YYYY-MM-DD
    added_price REAL,                    -- 加自选时现价(看"自选以来"涨跌)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    sort_order REAL DEFAULT 0,            -- 全局手动位次(「全部」视图用; 与分组无关)
    UNIQUE(user_id, stock_code)
);

-- 观察池的分组成员表: 一行 = 这只票在这个分组里。一票多组。
-- 为什么不是 watchlist 上的一列: 单值列只能表达"属于一个组", 而同一只票常常既属于
-- 「高端内存」又属于「博弈」。组内手动位次同理 —— 每个组各有一份顺序, 挂在 watchlist
-- 上的话一只票只能有一个位次, 进第二个组就必然跟第一个组抢。
-- 默认组「自选」在这张表里没有行: 一只票在本表里查不到任何分组 = 还没归组。
CREATE TABLE IF NOT EXISTS watchlist_group (
    user_id INTEGER NOT NULL REFERENCES users(id),
    stock_code TEXT NOT NULL,
    grp TEXT NOT NULL,                   -- 分组名(非空; 空串是"未归组", 不占行)
    group_order REAL DEFAULT 0,          -- 该组内的手动位次(各组独立)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, stock_code, grp)
);

-- 全市场代码↔名称字典(个股 + 场内基金): 搜股的匹配底表。
-- 现拉一次要 20s+(stock_info_a_code_name 17 个分页 + ETF 表), 每次重启都重付, 首次
-- 搜索被阻塞几十秒。落盘后重启即时可用, 过期只在后台刷。
CREATE TABLE IF NOT EXISTS symbol_dict (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    is_etf INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 板块成交额份额逐日档案(收盘后定格): 看资金往哪个板块聚拢/撤离
CREATE TABLE IF NOT EXISTS sector_share_history (
    snap_date TEXT,                      -- YYYY-MM-DD
    board TEXT,                          -- 同花顺行业板块名
    amount_yi REAL,                      -- 板块总成交额(亿)
    share_pct REAL,                      -- 占全市场板块总成交比重(%)
    PRIMARY KEY (snap_date, board)
);

-- 市场量能逐日档案(沪/深/创业/科创 量+额): 东财可达时回填历史, 收盘后定格当日
CREATE TABLE IF NOT EXISTS market_volume_history (
    snap_date TEXT,                      -- YYYY-MM-DD
    market TEXT,                         -- 沪/深/创业/科创
    vol REAL,                            -- 成交量(股)
    amt REAL,                            -- 成交额(元)
    PRIMARY KEY (snap_date, market)
);

-- 市场情绪逐日档案(交易日收盘后定格, 情绪周期时间轴用)
CREATE TABLE IF NOT EXISTS sentiment_history (
    snap_date TEXT PRIMARY KEY,          -- YYYY-MM-DD
    n_zt INTEGER, n_dt INTEGER, n_zb INTEGER,
    zbl_rate REAL, max_lb INTEGER, money_effect REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 逐日逐只涨停档案。sentiment_history 只记「几个涨停」, 这里记「涨停的质量」:
-- 封单额(买一挂单额, 真实盘口量) + 首次封板时刻 + 炸板次数。52 个涨停配 57 亿封单
-- 和 52 个涨停配 20 亿封单是两个完全不同的盘, 只数一样看不出来。
-- seal_amount 已用新浪盘口买一逐只验过(东财 49 只零偏离; 扩展源历史 79 只与东财全等)。
CREATE TABLE IF NOT EXISTS limit_up_pool (
    snap_date TEXT NOT NULL,             -- YYYY-MM-DD
    stock_code TEXT NOT NULL,
    name TEXT,
    seal_amount REAL,                    -- 封单额(元) = 买一量 × 买一价
    first_seal TEXT,                     -- 首次封板 HH:MM:SS ('09:25:00'=集合竞价一字板)
    last_seal TEXT,                      -- 最后封板 HH:MM:SS (与首封不同 = 中间开过板)
    lb_count INTEGER,                    -- 连板数
    broken_times INTEGER,                -- 炸板次数(仅东财有, 扩展源那份留空)
    zt_days INTEGER, zt_ct INTEGER,      -- N 天 M 板(仅东财)
    industry TEXT, theme TEXT,
    amount REAL,                         -- 成交额(元)
    float_mv REAL,                       -- 流通市值(元, 仅东财)
    turnover REAL,                       -- 换手%(仅东财)
    pct REAL,                            -- 当日涨跌幅%
    source TEXT,                         -- em(日常, 字段全) | provider(扩展源一次性历史回填;
                                         --   provider 化之前的老库里这一列可能还是 'kpl')
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (snap_date, stock_code)
);
CREATE INDEX IF NOT EXISTS idx_lup_date ON limit_up_pool(snap_date);

-- 归类层: 日期 × 代码 × 各轴的格子。归类跑一次落这儿, 所有面板与 agent 读同一份。
--
-- 为什么要单独一层: 项目里原来有**五套各自为政**的归类(concept_tags 概念 / market_review
-- 行业+概念 / limit_up_pool 行业+题材 / sector_share 行业 / concept_trend 概念), 维度不统一
-- 且一处都没落库 —— 同一只票在五个面板里可能落进五个不同名字, 也没法问"它昨天在哪个格子里"。
-- 有了这一层才能问格子之间的**迁移**(首板→二连板转化率、钱从哪个题材挪到哪个)。
--
-- 为什么不并进 limit_up_pool: 「资金归类」是全市场成交额前 N, **不看是否涨停** —— 一只票
-- 可以在钱轴上有格子而当天没涨停, 涨停档案装不下它。
--
-- 各轴的覆盖不一样, 别当成一样齐: 板/进/题 覆盖 244 天涨停股全量(从涨停档案回放);
-- 钱轴只有当日榜单能给, 历史那 244 天拉不到全市场成交额排名(kline_cache 只有 31% 代码,
-- 拿它排会排出一个假榜), 所以历史留空、从落这层之后每天收盘攒。
CREATE TABLE IF NOT EXISTS stock_tag (
    snap_date TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    name TEXT,
    -- 板: 当日连板数(1=首板)。NULL = 当日没涨停(它可能只是钱轴上榜)
    lb_count INTEGER,
    -- 进: 我方口径的「N天M板」= 断 ≤2 个交易日 + 回溯 20 日。
    -- 与东财 zttj 在 1065 行标注上完全命中 95.3%, 「是不是新面孔」这一项 97.8%
    -- (只看 lb_count 是 90.8%)。失败方向单一: 我们**少算**, 会把少数高位老票读成新面孔。
    jj_days INTEGER, jj_boards INTEGER,
    -- 1 = 回溯窗口撞到档案起点, 这一格的 进 不可信(档案最早 20 个交易日会这样)
    jj_partial INTEGER DEFAULT 0,
    -- 钱: 全市场成交额位次(1=最高)与成交额(元)。不看是否涨停
    amt_rank INTEGER, amt REAL,
    -- 题: 统一后的题材/行业, 一套口径
    theme TEXT, industry TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (snap_date, stock_code)
);
CREATE INDEX IF NOT EXISTS idx_tag_date ON stock_tag(snap_date);
CREATE INDEX IF NOT EXISTS idx_tag_code ON stock_tag(stock_code);

CREATE TABLE IF NOT EXISTS cashflow_monthly (
    user_id INTEGER NOT NULL REFERENCES users(id),
    month TEXT NOT NULL,             -- YYYY-MM
    income REAL DEFAULT 0,              -- 月收入(税后)
    fixed_cost REAL DEFAULT 0,          -- 固定开销 (房租/餐饮/账单/还贷)
    discretionary REAL DEFAULT 0,       -- 实际可自由支配开销 (购物/娱乐/旅行)
    notes TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, month)
);

-- 外部资产 (基金/加密/理财/现金) 的交易流水, 用于 FIFO 算实现盈亏.
-- BOT 不走这张表 (走 OKX 同步).
CREATE TABLE IF NOT EXISTS external_asset_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    asset_id INTEGER NOT NULL,           -- FK external_assets.id
    action_type TEXT NOT NULL,           -- BUY | ADD | REDEEM | DEPOSIT | WITHDRAW | INTEREST | DIVIDEND
    amount REAL NOT NULL DEFAULT 0,      -- CNY 本金/赎回金额(总额); INTEREST/DIVIDEND 时为派息金额
    shares REAL,                         -- FUND/CRYPTO 用; +加仓 / -赎回; WEALTH/CASH 留空
    unit_price REAL,                     -- FUND/CRYPTO 当时净值/价
    fee REAL DEFAULT 0,                  -- 手续费 (CNY), 含在 amount 里 (amount = 总付出含费)
    trade_date TEXT,                     -- YYYY-MM-DD (申请日)
    status TEXT DEFAULT 'confirmed',     -- confirmed: 进 ledger; pending: T+1 待确认 (OTC 基金 申购/赎回)
    note TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id, asset_id) REFERENCES external_assets(user_id, id)
);
CREATE INDEX IF NOT EXISTS idx_eaa_asset_date ON external_asset_actions (asset_id, trade_date);

-- 定投计划: 按 frequency 触发, 每次写一条 pending ADD action.
CREATE TABLE IF NOT EXISTS dca_schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER NOT NULL,
    mode TEXT NOT NULL DEFAULT 'amount',     -- 'amount' (固定金额) | 'shares' (固定份额)
    value REAL NOT NULL,                     -- amount=CNY; shares=份数
    frequency TEXT NOT NULL DEFAULT 'monthly', -- 'daily_trading' | 'weekly' | 'monthly'
    day_of_month INTEGER,                    -- 1-31 (frequency=monthly), 月末 clamp
    day_of_week INTEGER,                     -- 1=Mon..7=Sun (frequency=weekly)
    status TEXT NOT NULL DEFAULT 'active',   -- 'active' | 'paused'
    next_due TEXT,                           -- YYYY-MM-DD
    last_fired_at TEXT,                      -- YYYY-MM-DD
    note TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dca_status_due ON dca_schedules (status, next_due);

-- 持仓逻辑跟踪: 记录"当初为什么买这只", 供复盘客观对照逻辑是否还成立 (A股 + 场内外资产共用 code)。
CREATE TABLE IF NOT EXISTS position_thesis (
    user_id INTEGER NOT NULL REFERENCES users(id),
    code TEXT NOT NULL,                    -- 股票/基金代码 (裸码, 如 600519 / 159516)
    name TEXT DEFAULT '',
    thesis TEXT NOT NULL,                     -- 买入逻辑 (为什么买、看中什么、预期)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, code)
);

-- 逻辑修订史(append-only)。position_thesis 只留最新一版, 覆盖写会把"改过什么"这件事本身抹掉 ——
-- 而最值得看的恰恰是它: 人跌了之后会不自觉地把买入理由换成一个能解释当前股价的说法, 事后回看
-- 只见一句自洽的逻辑, 看不见它被改过三次。facts 存"写这段话那一刻的客观事实快照", 有它才分得清
-- 「事实变了所以改逻辑」和「事实没变只是改了说法」。
CREATE TABLE IF NOT EXISTS thesis_revision (
    user_id INTEGER NOT NULL REFERENCES users(id),
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    rev INTEGER NOT NULL,                     -- 1 起递增
    thesis TEXT NOT NULL,
    facts TEXT DEFAULT '',                    -- JSON 快照; '' = 当时没记(老数据/取数失败), 不是"没变化"
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, code, rev)
);
CREATE INDEX IF NOT EXISTS idx_thesis_rev_code ON thesis_revision(code, rev);

CREATE TABLE IF NOT EXISTS ask_session (
    user_id INTEGER NOT NULL REFERENCES users(id),
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT DEFAULT '',                    -- 用首个问题做标题
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id,id)
);

CREATE TABLE IF NOT EXISTS ask_message (
    user_id INTEGER NOT NULL REFERENCES users(id),
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    role TEXT NOT NULL,                        -- user | assistant
    content TEXT NOT NULL,
    meta TEXT DEFAULT '',                      -- JSON: {tools_used, sources}
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id,session_id) REFERENCES ask_session(user_id,id)
);
CREATE INDEX IF NOT EXISTS idx_ask_message_session ON ask_message(session_id, id);

-- 一轮 agent 执行的过程流水。答案本身进 ask_message, 这张表存的是"跑的过程":
-- 调了哪些工具、画了什么图、中途报了什么错。
-- 为什么要落盘: run 本来只活在内存里, 服务一重启(改代码/掉电)在跑的那轮就地消失 ——
-- 界面上是一行永远转不完的"分析中", 而且连它已经查过什么都看不到。落了盘之后, 重启后
-- 那一轮会被标成 interrupted, 已经跑出来的步骤还在, 至少知道它走到哪一步。
CREATE TABLE IF NOT EXISTS ask_run (
    user_id INTEGER NOT NULL REFERENCES users(id),
    run_id TEXT PRIMARY KEY,
    session_id INTEGER,
    scope TEXT DEFAULT '',                     -- market | stock:<code>
    question TEXT DEFAULT '',
    status TEXT DEFAULT 'running',             -- running | done | interrupted
    answered INTEGER DEFAULT 0,
    events TEXT DEFAULT '[]',                  -- JSON 数组, 下标即游标
    started_at REAL,
    updated_at REAL,
    FOREIGN KEY(user_id,session_id) REFERENCES ask_session(user_id,id)
);
CREATE INDEX IF NOT EXISTS idx_ask_run_started ON ask_run(started_at DESC);

-- 工具缺口台账: agent 调工具时取不到数就记一笔, 攒成清单。
-- 为什么要它: 实测 agent 的错误质量集中在取数层而不是推理层 —— 一次典型的翻车是
-- get_news 对美股返回了 10 条(所以模型以为消息面已覆盖), 但最新一条比异动早三天;
-- 另一次是美股行情把 开/高/低 全返 0(源里其实有)。这类"看着成功其实没数"的情况
-- 不记账就永远只能等用户发现。按 (工具, 缺口类型, 市场) 聚合计数, 不存每次调用。
CREATE TABLE IF NOT EXISTS tool_gap (
    user_id INTEGER NOT NULL REFERENCES users(id),
    tool TEXT NOT NULL,
    kind TEXT NOT NULL,                        -- error | empty | stale | zero_fields
    market TEXT NOT NULL DEFAULT '',           -- A | HK | US | '' (与标的无关的工具)
    hits INTEGER NOT NULL DEFAULT 0,
    detail TEXT DEFAULT '',                    -- 最近一次的说明 (如 "latest_time 早 3 天")
    sample TEXT DEFAULT '',                    -- 最近一次的入参样本
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, tool, kind, market)
);

-- 从"用户纠正"沉淀出来的候选规则, 人批准后才进 system prompt。
-- 为什么要闸门: prompt 里已有 56 条规则, 自动往里加最容易造成规则互撞, 而且 prompt
-- 对单个词都敏感(曾因词表缺"主题/标题"两个词让模型吞掉首句)。所以只自动"提案", 不自动
-- 生效; status=pending 的一律不进 prompt。
CREATE TABLE IF NOT EXISTS prompt_rule (
    user_id INTEGER NOT NULL REFERENCES users(id),
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,                       -- 【】里的标题
    body TEXT NOT NULL,                        -- 规则正文(正向陈述)
    status TEXT NOT NULL DEFAULT 'pending',    -- pending | active | rejected
    evidence TEXT DEFAULT '',                  -- 触发它的那次纠正原文(截断)
    session_id INTEGER,                        -- 来自哪个会话
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    decided_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_prompt_rule_status ON prompt_rule(status, id);
"""


ACCOUNT_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT UNIQUE NOT NULL,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'user')),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    must_change_password INTEGER NOT NULL DEFAULT 0 CHECK (must_change_password IN (0, 1)),
    auth_version INTEGER NOT NULL DEFAULT 1 CHECK (auth_version > 0),
    password_reset_at INTEGER,
    created_at INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS users_single_admin ON users(role) WHERE role = 'admin';

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    token_hash TEXT UNIQUE NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(id),
    auth_version INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL CHECK (expires_at > created_at),
    revoked_at INTEGER
);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS bootstrap_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    token_hash TEXT NOT NULL,
    token_file TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL CHECK (expires_at > created_at),
    consumed_at INTEGER
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    outcome TEXT NOT NULL,
    actor_id INTEGER,
    target_id INTEGER,
    reason TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_events_created ON audit_events(created_at);
"""


async def get_db() -> aiosqlite.Connection:
    from services import runtime_state
    import sqlite3
    key,generation=runtime_state.acquire_connection()
    db=None
    managed=False
    try:
        path = database_path(config.db_path)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not path.exists():path.touch(mode=0o600, exist_ok=True)
        db = await aiosqlite.connect(path,cached_statements=0)
        db.row_factory = aiosqlite.Row
        await db.execute('PRAGMA foreign_keys = ON')
        def authorize(*args):
            try:
                runtime_state.check_generation(generation)
                return sqlite3.SQLITE_OK
            except runtime_state.RuntimeUnavailable:
                return sqlite3.SQLITE_DENY
        await db.set_authorizer(authorize)
        original_close=db.close
        closed=False
        async def close():
            nonlocal closed
            if closed:return
            closed=True
            import asyncio
            operation=asyncio.create_task(original_close())
            try:
                await asyncio.shield(operation)
            except asyncio.CancelledError:
                await operation
                raise
            finally:runtime_state.release_connection(key)
        db.close=close
        managed=True
        runtime_state.check_generation(generation)
        return db
    except BaseException:
        if db is not None:await db.close()
        if not managed:runtime_state.release_connection(key)
        raise


async def _portfolio_upgrade_tables(db) -> list[str]:
    """只升级空的旧业务表；账号信息保持原样，不猜测历史业务 owner。"""
    old = []
    for table in OWNED_PORTFOLIO_TABLES + OWNED_SETTINGS_TABLES + OWNED_ASK_TABLES:
        columns = await (await db.execute(f'PRAGMA table_info("{table}")')).fetchall()
        if columns and 'user_id' not in {row['name'] for row in columns}:
            if table == 'brokers':
                rows = await (await db.execute('SELECT name, stock_rate, stock_min, etf_rate, etf_min, is_default FROM brokers ORDER BY id')).fetchall()
                if rows and [tuple(r) for r in rows] != list(DEFAULT_BROKERS):
                    raise RuntimePathError('发现无归属历史券商配置')
            elif await (await db.execute(f'SELECT 1 FROM "{table}" LIMIT 1')).fetchone():
                raise RuntimePathError('发现无归属历史业务数据，请使用新的空运行目录，不自动分配给管理员')
            old.append(table)
    metadata = await (await db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='runtime_metadata'")).fetchone()
    if metadata:
        version = await (await db.execute("SELECT value FROM runtime_metadata WHERE key='portfolio_schema_version'")).fetchone()
        if version and version[0] != PORTFOLIO_SCHEMA_VERSION:
            raise RuntimePathError('持仓归属数据库版本不兼容，停止初始化')
    return old


async def init_db():
    paths = get_runtime_paths()
    if paths.profile != "test":
        paths.check_legacy_database()
        paths.check_database(database_path(config.db_path))
    db = await get_db()
    try:
        metadata = await (await db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='runtime_metadata'")).fetchone()
        if metadata:
            version = await (await db.execute("SELECT value FROM runtime_metadata WHERE key='account_schema_version'")).fetchone()
            if version and version[0] != ACCOUNT_SCHEMA_VERSION:
                raise RuntimePathError("账号数据库版本不兼容，停止初始化")
        old_tables = await _portfolio_upgrade_tables(db)
        await db.executescript(ACCOUNT_SCHEMA)
        drops = ''.join(f'DROP TABLE "{table}";\n' for table in old_tables)
        await db.executescript('BEGIN IMMEDIATE;\n' + drops + SCHEMA + '\nCOMMIT;')
        await db.execute('CREATE TABLE IF NOT EXISTS runtime_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        await db.execute("INSERT OR IGNORE INTO runtime_metadata VALUES ('layout_version', ?)", (LAYOUT_VERSION,))
        await db.execute("INSERT OR IGNORE INTO runtime_metadata VALUES ('account_schema_version', ?)", (ACCOUNT_SCHEMA_VERSION,))
        await db.execute("INSERT OR IGNORE INTO runtime_metadata VALUES ('portfolio_schema_version', ?)", (PORTFOLIO_SCHEMA_VERSION,))
        for key in ('settings_schema_version','ask_schema_version'):
            version = await (await db.execute('SELECT value FROM runtime_metadata WHERE key=?',(key,))).fetchone()
            if version and version[0] != '1':
                raise RuntimePathError('私人业务数据库版本不兼容')
            if key == 'settings_schema_version' and not version:
                users = await (await db.execute('SELECT id FROM users')).fetchall()
                for user in users:
                    await db.executemany('INSERT OR IGNORE INTO brokers(user_id,name,stock_rate,stock_min,etf_rate,etf_min,is_default) VALUES (?,?,?,?,?,?,?)',[(user['id'],*row) for row in DEFAULT_BROKERS])
            await db.execute('INSERT OR IGNORE INTO runtime_metadata(key,value) VALUES (?,?)',(key,'1'))
        # Migration: add trade_date to position_actions if missing
        cursor = await db.execute('PRAGMA table_info(position_actions)')
        cols = {row[1] for row in await cursor.fetchall()}
        if "trade_date" not in cols:
            await db.execute('ALTER TABLE position_actions ADD COLUMN trade_date TEXT')
        if "fee" not in cols:
            # NULL = 用 estimate_trade_fee 自动算; 非 NULL = 用户手填覆盖
            await db.execute('ALTER TABLE position_actions ADD COLUMN fee REAL')
        if "trade_time" not in cols:
            # 成交时刻 HH:MM (可选); NULL → 用 created_at(转北京时间)推断, 供分时图精准打 B/S 点
            await db.execute('ALTER TABLE position_actions ADD COLUMN trade_time TEXT')
        if "broker" not in cols:
            # 本笔成交的券商 (可选); NULL → 用持仓默认券商。支持同股跨券商, 手续费按各自费率
            await db.execute('ALTER TABLE position_actions ADD COLUMN broker TEXT')
        if "fx_rate" not in cols:
            # 港美股成交日汇率(每 1 原币折人民币); NULL → 按当前汇率折算(A 股不用)
            await db.execute('ALTER TABLE position_actions ADD COLUMN fx_rate REAL')
        # Migration: kline_cache 加成交额列(老库只有 volume)
        cursor = await db.execute('PRAGMA table_info(kline_cache)')
        kc_cols = {row[1] for row in await cursor.fetchall()}
        if "amount" not in kc_cols:
            await db.execute('ALTER TABLE kline_cache ADD COLUMN amount REAL DEFAULT 0')
        # Migration: add trade_time to external_asset_actions
        cursor = await db.execute('PRAGMA table_info(external_asset_actions)')
        eaa_cols = {row[1] for row in await cursor.fetchall()}
        if "trade_time" not in eaa_cols:
            await db.execute('ALTER TABLE external_asset_actions ADD COLUMN trade_time TEXT')
        # Migration: add purchase_date to holdings if missing (reserved for future)
        cursor = await db.execute('PRAGMA table_info(holdings)')
        cols = {row[1] for row in await cursor.fetchall()}
        if "purchase_date" not in cols:
            await db.execute('ALTER TABLE holdings ADD COLUMN purchase_date TEXT')
        # Migration: add sold_back fields to unwind_tranches for T-sell tracking
        cursor = await db.execute('PRAGMA table_info(unwind_tranches)')
        cols = {row[1] for row in await cursor.fetchall()}
        if "sold_back_price" not in cols:
            await db.execute('ALTER TABLE unwind_tranches ADD COLUMN sold_back_price REAL')
        if "sold_back_at" not in cols:
            await db.execute('ALTER TABLE unwind_tranches ADD COLUMN sold_back_at TIMESTAMP')
        # Migration: add okx_algo_id + okx_bot_type to external_assets for auto-sync
        cursor = await db.execute('PRAGMA table_info(external_assets)')
        cols = {row[1] for row in await cursor.fetchall()}
        if "okx_algo_id" not in cols:
            await db.execute('ALTER TABLE external_assets ADD COLUMN okx_algo_id TEXT')
        if "okx_bot_type" not in cols:
            await db.execute('ALTER TABLE external_assets ADD COLUMN okx_bot_type TEXT')
        # WEALTH 类型用：年化收益率 + 起投日 → 自动算当前总额
        if "annual_yield_rate" not in cols:
            await db.execute('ALTER TABLE external_assets ADD COLUMN annual_yield_rate REAL')
        if "start_date" not in cols:
            await db.execute('ALTER TABLE external_assets ADD COLUMN start_date TEXT')
        # 基金/加密 待确认份额：买了但份额还没结算
        if "pending_amount" not in cols:
            await db.execute('ALTER TABLE external_assets ADD COLUMN pending_amount REAL DEFAULT 0')
        # OKX 马丁实际总预算 (USDT). raw 字段没"总预算", 算法反推不准, 让用户手填覆盖.
        if "bot_budget_override_usdt" not in cols:
            await db.execute('ALTER TABLE external_assets ADD COLUMN bot_budget_override_usdt REAL')
        # 基金申购费率 (小数, 如 0.0015 = 0.15%). C 类/无申购费 = 0/NULL. 批量结算时内扣算份额。
        if "purchase_fee_rate" not in cols:
            await db.execute('ALTER TABLE external_assets ADD COLUMN purchase_fee_rate REAL')

        cur = await db.execute('PRAGMA table_info(holdings)')
        hcols = {r[1] for r in await cur.fetchall()}
        if "broker" not in hcols:
            await db.execute('ALTER TABLE holdings ADD COLUMN broker TEXT')
        cur = await db.execute('PRAGMA table_info(external_assets)')
        ecols = {r[1] for r in await cur.fetchall()}
        if "broker" not in ecols:
            await db.execute('ALTER TABLE external_assets ADD COLUMN broker TEXT')
        # 平仓归档(BOT/策略止损结束等): closed=1 退出在持, closed_realized 记平仓时已实现盈亏(CNY)
        if "closed" not in ecols:
            await db.execute('ALTER TABLE external_assets ADD COLUMN closed INTEGER DEFAULT 0')
        if "closed_realized" not in ecols:
            await db.execute('ALTER TABLE external_assets ADD COLUMN closed_realized REAL')
        if "closed_date" not in ecols:
            await db.execute('ALTER TABLE external_assets ADD COLUMN closed_date TEXT')

        # external_asset_actions: status 字段 + fee 字段 (旧库迁移)
        cursor = await db.execute('PRAGMA table_info(external_asset_actions)')
        cols = {row[1] for row in await cursor.fetchall()}
        if cols and "status" not in cols:
            await db.execute("ALTER TABLE external_asset_actions ADD COLUMN status TEXT DEFAULT 'confirmed'")
        if cols and "fee" not in cols:
            # 手续费 (CNY), 包含在 amount 里 (amount = 总付出含费), 单存方便看净额
            await db.execute('ALTER TABLE external_asset_actions ADD COLUMN fee REAL DEFAULT 0')
        if cols and "interest_part" not in cols:
            # WEALTH/CASH 赎回时拆分: amount 里有多少是利息. NULL=不区分(走 FIFO 兜底)
            await db.execute('ALTER TABLE external_asset_actions ADD COLUMN interest_part REAL')

        # dca_schedules: 旧库迁移 frequency / day_of_week / day_of_month nullable
        cursor = await db.execute('PRAGMA table_info(dca_schedules)')
        cols = {row[1] for row in await cursor.fetchall()}
        if cols:
            if "frequency" not in cols:
                await db.execute("ALTER TABLE dca_schedules ADD COLUMN frequency TEXT NOT NULL DEFAULT 'monthly'")
            if "day_of_week" not in cols:
                await db.execute('ALTER TABLE dca_schedules ADD COLUMN day_of_week INTEGER')
        await db.commit()

        # Seed: any holding without a position_action → create initial BUY action
        cursor = await db.execute("""
            SELECT h.user_id, h.stock_code, h.shares, h.cost_price, h.created_at
            FROM holdings h
            WHERE NOT EXISTS (
                SELECT 1 FROM position_actions a WHERE a.stock_code = h.stock_code AND a.user_id = h.user_id
            )
        """)
        rows = await cursor.fetchall()
        for r in rows:
            code = r["stock_code"]
            shares = r["shares"]
            cost = r["cost_price"]
            created = str(r["created_at"])[:10] if r["created_at"] else None
            await db.execute(
                """INSERT INTO position_actions
                   (user_id, stock_code, action_type, price, shares, note, trade_date)
                   VALUES (?, ?, 'BUY', ?, ?, 'initial (auto-migrated)', ?)""",
                (r["user_id"], code, cost, shares, created),
            )
            print(f"[migration] Seeded initial BUY for {code}: {shares}股 @ {cost} on {created}")
        await db.commit()

        # Seed external_asset_actions: 给已有 external_assets 还没 actions 的, 按当前 cost_amount 补一条 BUY/DEPOSIT
        cursor = await db.execute("""
            SELECT a.user_id, a.id, a.asset_type, a.cost_amount, a.shares, a.start_date, a.created_at
            FROM external_assets a
            WHERE NOT EXISTS (
                SELECT 1 FROM external_asset_actions ea WHERE ea.asset_id = a.id AND ea.user_id = a.user_id
            )
        """)
        rows = await cursor.fetchall()
        for r in rows:
            asset_id = r["id"]
            atype = r["asset_type"] or ""
            cost = float(r["cost_amount"] or 0)
            shares = r["shares"]
            unit_price = None
            if atype in ("FUND", "CRYPTO") and shares and float(shares) > 0:
                unit_price = round(cost / float(shares), 6) if cost > 0 else None
            action_type = "BUY" if atype in ("FUND", "CRYPTO", "BOT") else "DEPOSIT"
            seed_date = (r["start_date"] or str(r["created_at"] or "")[:10]) or None
            await db.execute(
                """INSERT INTO external_asset_actions
                   (user_id, asset_id, action_type, amount, shares, unit_price, trade_date, note)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'initial (auto-migrated)')""",
                (r["user_id"], asset_id, action_type, cost, float(shares) if shares else None, unit_price, seed_date),
            )
            print(f"[migration] Seeded {action_type} for asset#{asset_id} ({atype}): ¥{cost} on {seed_date}")
        await db.commit()

        await db.execute("INSERT INTO thesis_revision(user_id,code,rev,thesis,facts,created_at) SELECT t.user_id,t.code,1,t.thesis,'',t.created_at FROM position_thesis t WHERE NOT EXISTS (SELECT 1 FROM thesis_revision r WHERE r.user_id=t.user_id AND r.code=t.code)")
        await db.commit()
    finally:
        await db.close()


# --- Holdings CRUD ---

async def get_all_holdings(*, user_id: int) -> list[dict]:
    validate_user_id(user_id)
    db = await get_db()
    try:
        cursor = await db.execute('SELECT * FROM holdings WHERE user_id = ? ORDER BY stock_code', (user_id,))
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


async def get_holding(stock_code: str, *, user_id: int) -> dict | None:
    validate_user_id(user_id)
    db = await get_db()
    try:
        cursor = await db.execute('SELECT * FROM holdings WHERE stock_code = ? AND user_id = ?', (stock_code, user_id))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def add_holding(stock_code: str, stock_name: str, shares: int, cost_price: float, *, user_id: int):
    validate_user_id(user_id)
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO holdings (user_id, stock_code, stock_name, shares, cost_price) VALUES (?, ?, ?, ?, ?)",
            (user_id, stock_code, stock_name, shares, cost_price),
        )
        await db.commit()
    finally:
        await db.close()


async def update_holding(stock_code: str, *, user_id: int, **kwargs):
    validate_user_id(user_id)
    db = await get_db()
    try:
        sets = []
        vals = []
        if set(kwargs) - {"stock_name", "shares", "cost_price", "purchase_date", "broker"}:
            raise ValueError("不允许更新该持仓字段")
        for k, v in kwargs.items():
            if v is not None:
                sets.append(f"{k} = ?")
                vals.append(v)
        if not sets:
            return
        sets.append("updated_at = CURRENT_TIMESTAMP")
        vals.extend((stock_code, user_id))
        await db.execute(
            f"UPDATE holdings SET {', '.join(sets)} WHERE stock_code = ? AND user_id = ?",
            vals,
        )
        await db.commit()
    finally:
        await db.close()


async def delete_holding(stock_code: str, *, user_id: int):
    validate_user_id(user_id)
    db = await get_db()
    try:
        await db.execute('DELETE FROM holdings WHERE stock_code = ? AND user_id = ?', (stock_code, user_id))
        await db.commit()
    finally:
        await db.close()


# --- K-line Cache ---

async def get_cached_klines(stock_code: str, limit: int = 250) -> list[dict]:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT date, open, high, low, close, volume, COALESCE(amount,0) AS amount "
            "FROM kline_cache WHERE stock_code = ? ORDER BY date DESC LIMIT ?",
            (stock_code, limit),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in reversed(rows)]
    finally:
        await db.close()


async def get_cached_amounts(codes: list[str], since: str) -> dict[str, dict[str, float]]:
    """一次取多只票的逐日成交额 → {日期: {代码: 成交额}}。给"这条概念线前几天多少钱"用。

    逐只查会打出上百次往返(榜单 100 只 × 一周), 这里一条 IN 查询取回。
    """
    if not codes:
        return {}
    db = await get_db()
    try:
        out: dict[str, dict[str, float]] = {}
        for i in range(0, len(codes), 400):          # SQLite 变量上限 999, 分批
            chunk = codes[i:i + 400]
            q = ",".join("?" * len(chunk))
            cur = await db.execute(
                f"SELECT date, stock_code, COALESCE(amount,0) AS amount FROM kline_cache "
                f"WHERE stock_code IN ({q}) AND date >= ? AND COALESCE(amount,0) > 0",
                (*chunk, since))
            for r in await cur.fetchall():
                out.setdefault(r["date"], {})[r["stock_code"]] = float(r["amount"])
        return out
    finally:
        await db.close()


# ---- agent 执行流水(ask_run) ----

async def save_ask_run(run_id: str, session_id: int, scope: str, question: str, started_at: float, *, user_id: int):
    validate_user_id(user_id)
    db = await get_db()
    try:
        if not await (await db.execute('SELECT 1 FROM ask_session WHERE id=? AND user_id=?', (session_id,user_id))).fetchone():
            raise ValueError('会话不存在或不属于当前账号')
        await db.execute("INSERT INTO ask_run (user_id, run_id, session_id, scope, question, status, events, started_at, updated_at) VALUES (?, ?, ?, ?, ?, 'running', '[]', ?, ?)", (user_id, run_id, session_id, scope, question, started_at, started_at))
        await db.commit()
    finally:
        await db.close()


async def update_ask_run(run_id: str, events_json: str, status: str, answered: bool, now: float, *, user_id: int):
    validate_user_id(user_id)
    db = await get_db()
    try:
        await db.execute('UPDATE ask_run SET events = ?, status = ?, answered = ?, updated_at = ? WHERE user_id = ? AND run_id = ?', (events_json, status, 1 if answered else 0, now, user_id, run_id))
        await db.commit()
    finally:
        await db.close()


async def get_ask_run(run_id: str, *, user_id: int) -> dict | None:
    validate_user_id(user_id)
    db = await get_db()
    try:
        cur = await db.execute('SELECT * FROM ask_run WHERE user_id = ? AND run_id = ?', (user_id, run_id))
        r = await cur.fetchone()
        return dict(r) if r else None
    finally:
        await db.close()


async def list_ask_runs(limit: int=20, *, user_id: int) -> list[dict]:
    validate_user_id(user_id)
    db = await get_db()
    try:
        cur = await db.execute('SELECT run_id, session_id, scope, question, status, answered, started_at, updated_at FROM ask_run WHERE user_id = ? ORDER BY started_at DESC LIMIT ?', (user_id, limit))
        return [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()


async def interrupt_running_ask_runs() -> int:
    """启动时把上一进程留下的 running 全部标成 interrupted。

    进程一没, 那些 asyncio task 就不存在了 —— 留着 running 会让界面一直转, 而它永远不会有
    结果。这里不做"自动重跑": 重跑要再烧一遍 token, 该不该跑由人决定。
    """
    db = await get_db()
    try:
        cur = await db.execute("UPDATE ask_run SET status = 'interrupted' WHERE status = 'running'")
        await db.commit()
        return cur.rowcount or 0
    finally:
        await db.close()


async def prune_ask_runs(keep: int=200, *, user_id: int) -> None:
    """只留最近 keep 条流水(答案在 ask_message 里, 这张表是过程)。"""
    validate_user_id(user_id)
    db = await get_db()
    try:
        await db.execute('DELETE FROM ask_run WHERE user_id=? AND run_id NOT IN (SELECT run_id FROM ask_run WHERE user_id=? ORDER BY started_at DESC LIMIT ?)', (user_id, user_id, keep))
        await db.commit()
    finally:
        await db.close()


async def get_cached_closes(codes: list[str], day: str) -> dict[str, float]:
    """每只票在 day(含)之前最近一个交易日的收盘价 → {代码: 收盘}。回溯估值用。

    停牌/那天没数据的票取更早那根 —— 按当时能看到的最后价格算, 比直接漏掉整只票诚实。
    """
    if not codes:
        return {}
    db = await get_db()
    try:
        out: dict[str, float] = {}
        for i in range(0, len(codes), 400):
            chunk = codes[i:i + 400]
            q = ",".join("?" * len(chunk))
            cur = await db.execute(
                f"SELECT stock_code, close FROM kline_cache k WHERE stock_code IN ({q}) AND date <= ? "
                f"AND date = (SELECT MAX(date) FROM kline_cache k2 "
                f"            WHERE k2.stock_code = k.stock_code AND k2.date <= ?)",
                (*chunk, day, day))
            for r in await cur.fetchall():
                if r["close"]:
                    out[r["stock_code"]] = float(r["close"])
        return out
    finally:
        await db.close()


async def get_position_actions_until(day: str, *, user_id: int) -> dict[str, list[dict]]:
    """截至 day(含)的全部持仓动作, 按股票代码分组(时间升序)。回放账本还原当时的持股。"""
    validate_user_id(user_id)
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT * FROM position_actions WHERE user_id = ? AND COALESCE(trade_date, date(created_at)) <= ? "
            "ORDER BY COALESCE(trade_date, date(created_at)), id", (user_id, day))
        out: dict[str, list[dict]] = {}
        for r in await cur.fetchall():
            out.setdefault(r["stock_code"], []).append(dict(r))
        return out
    finally:
        await db.close()


async def get_snapshot_on_or_before(day: str, *, user_id: int) -> dict | None:
    """day(含)之前最近一条组合快照。外部资产(基金/理财/现金)没有可回溯的价格历史,
    只有这条快照记了当时的真实市值。"""
    validate_user_id(user_id)
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT snap_date, total_value, by_asset FROM portfolio_snapshots "
            "WHERE user_id = ? AND snap_date <= ? ORDER BY snap_date DESC LIMIT 1", (user_id, day))
        r = await cur.fetchone()
        return dict(r) if r else None
    finally:
        await db.close()


async def get_cached_latest_date(stock_code: str) -> str | None:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT MAX(date) as d FROM kline_cache WHERE stock_code = ?", (stock_code,)
        )
        row = await cursor.fetchone()
        return row["d"] if row and row["d"] else None
    finally:
        await db.close()


async def save_klines(stock_code: str, rows: list[dict]):
    if not rows:
        return
    db = await get_db()
    try:
        await db.executemany(
            "INSERT OR REPLACE INTO kline_cache (stock_code, date, open, high, low, close, volume, amount) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(stock_code, r["日期"], r["开盘"], r["最高"], r["最低"], r["收盘"],
              r.get("成交量", 0), r.get("成交额", 0) or 0) for r in rows],
        )
        await db.commit()
    finally:
        await db.close()


# --- Custom Alerts ---

async def get_custom_alerts(stock_code: str=None, enabled_only: bool=True, *, user_id: int) -> list[dict]:
    validate_user_id(user_id)
    db = await get_db()
    try:
        where = ['user_id = ?']
        params = [user_id]
        if stock_code:
            where.append('stock_code = ?')
            params.append(stock_code)
        if enabled_only:
            where.append('enabled = 1 AND triggered = 0')
        clause = f"WHERE {' AND '.join(where)}" if where else ''
        cursor = await db.execute(f'SELECT * FROM custom_alerts {clause} WHERE user_id = ? ORDER BY created_at DESC', (user_id, *params))
        return [dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()


async def add_custom_alert(stock_code: str, alert_type: str, price: float, message: str='', *, user_id: int):
    validate_user_id(user_id)
    db = await get_db()
    try:
        await db.execute('INSERT INTO custom_alerts (user_id, stock_code, alert_type, price, message) VALUES (?, ?, ?, ?, ?)', (user_id, stock_code, alert_type, price, message))
        await db.commit()
    finally:
        await db.close()


async def mark_alert_triggered(alert_id: int, *, user_id: int):
    validate_user_id(user_id)
    db = await get_db()
    try:
        await db.execute('UPDATE custom_alerts SET triggered = 1 WHERE user_id = ? AND id = ?', (user_id, alert_id))
        await db.commit()
    finally:
        await db.close()


async def delete_custom_alert(alert_id: int, *, user_id: int):
    validate_user_id(user_id)
    db = await get_db()
    try:
        await db.execute('DELETE FROM custom_alerts WHERE user_id = ? AND id = ?', (user_id, alert_id))
        await db.commit()
    finally:
        await db.close()


# --- App Config ---

async def get_config(key: str, *, user_id: int | None = None) -> str | None:
    if user_id is not None or key not in PUBLIC_SETTING_KEYS:
        from services.user_context import require_user_id
        return await get_user_setting(key, user_id=validate_user_id(user_id) if user_id is not None else require_user_id())
    db = await get_db()
    try:
        cursor = await db.execute('SELECT value FROM app_config WHERE key = ?', (key,))
        row = await cursor.fetchone()
        return row["value"] if row else None
    finally:
        await db.close()


async def set_config(key: str, value: str, *, user_id: int | None = None):
    if user_id is not None or key not in PUBLIC_SETTING_KEYS:
        from services.user_context import require_user_id
        return await set_user_setting(key, value, user_id=validate_user_id(user_id) if user_id is not None else require_user_id())
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO app_config (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
            (key, value, value),
        )
        await db.commit()
    finally:
        await db.close()


# ── 持仓逻辑 (thesis) ──
async def get_thesis(code: str, *, user_id: int) -> dict | None:
    validate_user_id(user_id)
    db = await get_db()
    try:
        cur = await db.execute('SELECT * FROM position_thesis WHERE user_id = ? AND code = ?', (user_id, code))
        row = await cur.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def list_theses(*, user_id: int) -> list[dict]:
    validate_user_id(user_id)
    db = await get_db()
    try:
        cur = await db.execute('SELECT code, name, thesis, updated_at FROM position_thesis WHERE user_id = ?', (user_id,))
        return [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()


async def set_thesis(code: str, thesis: str, name: str='', facts: str='', *, user_id: int) -> dict:
    """写入/更新买入逻辑, 并在**正文真的变了**的时候追加一条修订。

    只在正文变化时追加: 前端每次打开弹窗点保存都会 PUT 一次, 原文没动也记一版会把修订史灌成
    一堆同文, 漂移分析里"改过几次"就废了。facts 是调用方在保存那一刻取的事实快照(JSON 串),
    取不到就传空 —— 空要当"当时没记"处理, 不能当"事实没变"。
    """
    validate_user_id(user_id)
    db = await get_db()
    try:
        cur = await db.execute('SELECT thesis FROM position_thesis WHERE user_id = ? AND code = ?', (user_id, code))
        row = await cur.fetchone()
        changed = row is None or (row['thesis'] or '').strip() != (thesis or '').strip()
        await db.execute("INSERT INTO position_thesis (user_id, code, name, thesis, updated_at) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP) ON CONFLICT(user_id, code) DO UPDATE SET thesis = excluded.thesis, name = CASE WHEN excluded.name != '' THEN excluded.name ELSE position_thesis.name END, updated_at = CURRENT_TIMESTAMP", (user_id, code, name, thesis))
        rev = 0
        if changed:
            cur = await db.execute('SELECT COALESCE(MAX(rev), 0) FROM thesis_revision WHERE user_id = ? AND code = ?', (user_id, code))
            rev = int((await cur.fetchone())[0]) + 1
            await db.execute('INSERT INTO thesis_revision (user_id, code, rev, thesis, facts) VALUES (?, ?, ?, ?, ?)', (user_id, code, rev, thesis, facts or ''))
        await db.commit()
        return {'appended': changed, 'rev': rev}
    finally:
        await db.close()


async def get_thesis_revisions(code: str, *, user_id: int) -> list[dict]:
    """某只的全部修订, rev 升序(第 1 版在前)。"""
    validate_user_id(user_id)
    db = await get_db()
    try:
        cur = await db.execute('SELECT rev, thesis, facts, created_at FROM thesis_revision WHERE user_id = ? AND code = ? ORDER BY rev', (user_id, code))
        return [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()


async def delete_thesis(code: str, *, user_id: int):
    """清空逻辑。修订史一并删 —— 逻辑都不要了, 留着"改过几次"没有意义, 而且下次重记会从
    rev=N+1 接着编号, 看上去像是接着改的。"""
    validate_user_id(user_id)
    db = await get_db()
    try:
        await db.execute('DELETE FROM position_thesis WHERE user_id = ? AND code = ?', (user_id, code))
        await db.execute('DELETE FROM thesis_revision WHERE user_id = ? AND code = ?', (user_id, code))
        await db.commit()
    finally:
        await db.close()


# --- 问问市场 会话历史 CRUD ---

async def create_ask_session(title: str='', *, user_id: int) -> int:
    validate_user_id(user_id)
    db = await get_db()
    try:
        cur = await db.execute('INSERT INTO ask_session (user_id, title) VALUES (?, ?)', (user_id, title[:80]))
        await db.commit()
        return cur.lastrowid
    finally:
        await db.close()


async def add_ask_message(session_id: int, role: str, content: str, meta: str='', *, user_id: int) -> None:
    validate_user_id(user_id)
    db = await get_db()
    try:
        if not await (await db.execute('SELECT 1 FROM ask_session WHERE id=? AND user_id=?', (session_id,user_id))).fetchone():
            raise ValueError('会话不存在或不属于当前账号')
        await db.execute('INSERT INTO ask_message (user_id, session_id, role, content, meta) VALUES (?, ?, ?, ?, ?)', (user_id, session_id, role, content, meta or ''))
        await db.execute('UPDATE ask_session SET updated_at = CURRENT_TIMESTAMP WHERE user_id = ? AND id = ?', (user_id, session_id))
        await db.commit()
    finally:
        await db.close()


async def list_ask_sessions(limit: int=50, *, user_id: int) -> list[dict]:
    validate_user_id(user_id)
    db = await get_db()
    try:
        cur = await db.execute('SELECT s.id, s.title, s.created_at, s.updated_at, (SELECT COUNT(*) FROM ask_message m WHERE m.session_id = s.id) AS msg_count FROM ask_session s WHERE s.user_id=? AND EXISTS (SELECT 1 FROM ask_message m WHERE m.session_id = s.id AND m.user_id=s.user_id) ORDER BY s.updated_at DESC LIMIT ?', (user_id, limit))
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def get_ask_session(session_id: int, *, user_id: int) -> dict | None:
    validate_user_id(user_id)
    db = await get_db()
    try:
        cur = await db.execute('SELECT * FROM ask_session WHERE user_id = ? AND id = ?', (user_id, session_id))
        s = await cur.fetchone()
        if not s:
            return None
        cur = await db.execute('SELECT role, content, meta, created_at FROM ask_message WHERE user_id = ? AND session_id = ? ORDER BY id', (user_id, session_id))
        msgs = [dict(r) for r in await cur.fetchall()]
        return {**dict(s), 'messages': msgs}
    finally:
        await db.close()


async def delete_ask_session(session_id: int, *, user_id: int) -> None:
    validate_user_id(user_id)
    db = await get_db()
    try:
        await db.execute('DELETE FROM ask_message WHERE user_id = ? AND session_id = ?', (user_id, session_id))
        await db.execute('DELETE FROM ask_run WHERE session_id=? AND user_id=?', (session_id,user_id))
        await db.execute('DELETE FROM ask_session WHERE user_id = ? AND id = ?', (user_id, session_id))
        await db.commit()
    finally:
        await db.close()


# --- Unwind Plan CRUD ---

async def save_unwind_plan(stock_code: str, total_budget: float, status: str='active', *, user_id: int):
    validate_user_id(user_id)
    db = await get_db()
    try:
        await db.execute('INSERT INTO unwind_plans (user_id, stock_code, total_budget, status)\n               VALUES (?, ?, ?, ?)\n               ON CONFLICT(user_id, stock_code) DO UPDATE SET\n                 total_budget = excluded.total_budget,\n                 status = excluded.status,\n                 updated_at = CURRENT_TIMESTAMP', (user_id, stock_code, total_budget, status))
        await db.commit()
    finally:
        await db.close()


async def get_unwind_plan(stock_code: str, *, user_id: int) -> dict | None:
    validate_user_id(user_id)
    db = await get_db()
    try:
        cursor = await db.execute('SELECT * FROM unwind_plans WHERE user_id = ? AND stock_code = ?', (user_id, stock_code))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def get_all_unwind_plans(*, user_id: int) -> list[dict]:
    validate_user_id(user_id)
    db = await get_db()
    try:
        cursor = await db.execute('SELECT * FROM unwind_plans WHERE user_id = ? ORDER BY stock_code', (user_id,))
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def delete_unwind_plan(stock_code: str, *, user_id: int):
    validate_user_id(user_id)
    db = await get_db()
    try:
        await db.execute('DELETE FROM unwind_tranches WHERE user_id = ? AND stock_code = ?', (user_id, stock_code))
        await db.execute('DELETE FROM unwind_plans WHERE user_id = ? AND stock_code = ?', (user_id, stock_code))
        await db.commit()
    finally:
        await db.close()


async def update_unwind_used_budget(stock_code: str, used_budget: float, *, user_id: int):
    validate_user_id(user_id)
    db = await get_db()
    try:
        await db.execute('UPDATE unwind_plans SET used_budget = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ? AND stock_code = ?', (used_budget, user_id, stock_code))
        await db.commit()
    finally:
        await db.close()


# --- Tranche CRUD ---

async def add_tranche(stock_code: str, idx: int, trigger_price: float, shares: int, requires_health: str='any', *, user_id: int):
    validate_user_id(user_id)
    db = await get_db()
    try:
        await db.execute('INSERT INTO unwind_tranches (user_id, stock_code, idx, trigger_price, shares, requires_health)\n               VALUES (?, ?, ?, ?, ?, ?)', (user_id, stock_code, idx, trigger_price, shares, requires_health))
        await db.commit()
    finally:
        await db.close()


async def get_tranches(stock_code: str, *, user_id: int) -> list[dict]:
    validate_user_id(user_id)
    db = await get_db()
    try:
        cursor = await db.execute('SELECT * FROM unwind_tranches WHERE user_id = ? AND stock_code = ? ORDER BY idx', (user_id, stock_code))
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def get_tranche(tranche_id: int, *, user_id: int) -> dict | None:
    validate_user_id(user_id)
    db = await get_db()
    try:
        cursor = await db.execute('SELECT * FROM unwind_tranches WHERE user_id = ? AND id = ?', (user_id, tranche_id))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def clear_tranches(stock_code: str, *, user_id: int):
    validate_user_id(user_id)
    db = await get_db()
    try:
        await db.execute("DELETE FROM unwind_tranches WHERE user_id = ? AND stock_code = ? AND status = 'pending'", (user_id, stock_code))
        await db.commit()
    finally:
        await db.close()


async def mark_tranche_executed(tranche_id: int, executed_price: float, *, user_id: int):
    validate_user_id(user_id)
    db = await get_db()
    try:
        await db.execute("UPDATE unwind_tranches\n               SET status = 'executed', executed_price = ?, executed_at = CURRENT_TIMESTAMP\n               WHERE user_id = ? AND id = ?", (executed_price, user_id, tranche_id))
        await db.commit()
    finally:
        await db.close()


# --- External assets (ETFs / funds / crypto / bots) ---

async def list_external_assets(include_closed: bool = False, *, user_id: int) -> list[dict]:
    """在持外部资产(全系统'我的持仓/看板'的唯一来源)。默认排除已平仓归档的(closed=1),
    一处过滤处处生效; include_closed=True 才返回全部(含归档)。"""
    validate_user_id(user_id)
    db = await get_db()
    try:
        sql = "SELECT * FROM external_assets WHERE user_id = ?"
        if not include_closed:
            sql += " AND COALESCE(closed, 0) = 0"
        sql += " ORDER BY asset_type, id"
        cursor = await db.execute(sql, (user_id,))
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def list_closed_external_assets(*, user_id: int) -> list[dict]:
    """已平仓归档的外部资产(供已实现盈亏统计)。"""
    validate_user_id(user_id)
    db = await get_db()
    try:
        cursor = await db.execute('SELECT * FROM external_assets WHERE user_id = ? AND COALESCE(closed, 0) = 1 ORDER BY closed_date DESC', (user_id,))
        return [dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()


async def close_external_asset(asset_id: int, realized_cny: float, closed_date: str, *, user_id: int) -> None:
    """平仓归档: 标记 closed=1, 记下平仓时已实现盈亏(CNY), 市值归零(退出在持)。"""
    validate_user_id(user_id)
    db = await get_db()
    try:
        await db.execute(
            "UPDATE external_assets SET closed = 1, closed_realized = ?, closed_date = ?, "
            "manual_value = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?",
            (round(float(realized_cny), 2), closed_date, asset_id, user_id),
        )
        await db.commit()
    finally:
        await db.close()


async def get_external_asset(asset_id: int, *, user_id: int) -> dict | None:
    validate_user_id(user_id)
    db = await get_db()
    try:
        cursor = await db.execute('SELECT * FROM external_assets WHERE id = ? AND user_id = ?', (asset_id, user_id))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def add_external_asset(asset_type: str, code: str, name: str, platform: str,
                              cost_amount: float, shares: float | None = None,
                              manual_value: float | None = None, note: str = "",
                              okx_algo_id: str | None = None,
                              okx_bot_type: str | None = None,
                              annual_yield_rate: float | None = None,
                              start_date: str | None = None,
                              pending_amount: float | None = None,
                              purchase_fee_rate: float | None = None,
                              broker: str | None = None, *, user_id: int) -> int:
    validate_user_id(user_id)
    db = await get_db()
    try:
        cursor = await db.execute(
            """INSERT INTO external_assets
               (user_id, asset_type, code, name, platform, cost_amount, shares, manual_value, note,
                okx_algo_id, okx_bot_type, annual_yield_rate, start_date, pending_amount, purchase_fee_rate, broker)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, asset_type, code, name, platform, cost_amount, shares, manual_value, note,
             okx_algo_id, okx_bot_type, annual_yield_rate, start_date, pending_amount or 0, purchase_fee_rate, broker),
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


async def update_external_asset(asset_id: int, *, user_id: int, **kwargs):
    validate_user_id(user_id)
    if not kwargs:
        return
    if set(kwargs) - {"name", "platform", "cost_amount", "shares", "manual_value", "note", "okx_algo_id", "okx_bot_type", "annual_yield_rate", "start_date", "pending_amount", "purchase_fee_rate", "broker", "bot_budget_override_usdt", "closed", "closed_date", "closed_realized", "code", "asset_type"}:
        raise ValueError("不允许更新该资产字段")
    cols = ", ".join(f"{k} = ?" for k in kwargs)
    db = await get_db()
    try:
        await db.execute(
            f"UPDATE external_assets SET {cols}, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?",
            (*kwargs.values(), asset_id, user_id),
        )
        await db.commit()
    finally:
        await db.close()


async def delete_external_asset(asset_id: int, *, user_id: int):
    """删资产时连带清掉它的定投计划和流水。

    不连带的后果实测过: 计划还 active, 每天往已不存在的 asset_id 写一条 pending
    流水, settle-pending 又扫不到(它只遍历现存资产), 于是无限攒垃圾。
    流水离了资产在 UI 上也已经不可达。合并资产走 reassign_external_actions
    先搬走再删, 所以这里删到的只会是真正的孤儿。
    """
    validate_user_id(user_id)
    db = await get_db()
    try:
        if not await (await db.execute("SELECT 1 FROM external_assets WHERE id=? AND user_id=?", (asset_id, user_id))).fetchone():
            return False
        await db.execute('DELETE FROM dca_schedules WHERE asset_id = ?', (asset_id,))
        await db.execute('DELETE FROM external_asset_actions WHERE asset_id = ? AND user_id = ?', (asset_id, user_id))
        await db.execute('DELETE FROM external_assets WHERE id = ? AND user_id = ?', (asset_id, user_id))
        await db.commit()
    finally:
        await db.close()


async def reassign_external_actions(source_id: int, target_id: int, *, user_id: int) -> int:
    """把 source 资产的全部流水 + DCA 计划改挂到 target (合并重复资产用)。返回搬动的流水条数。"""
    validate_user_id(user_id)
    db = await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        for asset_id in (source_id, target_id):
            if not await (await db.execute("SELECT 1 FROM external_assets WHERE id=? AND user_id=?", (asset_id, user_id))).fetchone():
                raise ValueError("资产不存在或不属于当前账号")
        cur = await db.execute(
            "UPDATE external_asset_actions SET asset_id = ? WHERE asset_id = ? AND user_id = ?", (target_id, source_id, user_id))
        await db.execute(
            "UPDATE dca_schedules SET asset_id = ? WHERE asset_id = ?", (target_id, source_id))
        await db.commit()
        return cur.rowcount
    finally:
        await db.close()


async def mark_tranche_sold_back(tranche_id: int, sold_price: float, *, user_id: int):
    """Record the sell-leg of a tranche (做T 回收). Tranche remains 'executed'
    so it stays in the ladder; status of sell leg is tracked via sold_back_price."""
    validate_user_id(user_id)
    db = await get_db()
    try:
        await db.execute('UPDATE unwind_tranches\n               SET sold_back_price = ?, sold_back_at = CURRENT_TIMESTAMP\n               WHERE user_id = ? AND id = ?', (sold_price, user_id, tranche_id))
        await db.commit()
    finally:
        await db.close()


async def clear_tranche_sold_back(tranche_id: int, *, user_id: int):
    """Undo the sell leg."""
    validate_user_id(user_id)
    db = await get_db()
    try:
        await db.execute('UPDATE unwind_tranches SET sold_back_price = NULL, sold_back_at = NULL WHERE user_id = ? AND id = ?', (user_id, tranche_id))
        await db.commit()
    finally:
        await db.close()


# --- Position Actions Log ---

async def log_position_action(stock_code: str, action_type: str, price: float, shares: int,
                               tranche_id: int | None = None, note: str = "", *, user_id: int):
    validate_user_id(user_id)
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO position_actions (user_id, stock_code, action_type, price, shares, tranche_id, note)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, stock_code, action_type, price, shares, tranche_id, note),
        )
        await db.commit()
    finally:
        await db.close()


async def get_position_actions(stock_code: str = None, limit: int = 200, *, user_id: int) -> list[dict]:
    validate_user_id(user_id)
    db = await get_db()
    try:
        if stock_code:
            cursor = await db.execute(
                "SELECT * FROM position_actions WHERE user_id = ? AND stock_code = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, stock_code, limit),
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM position_actions WHERE user_id = ? ORDER BY created_at DESC LIMIT ?", (user_id, limit)
            )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


# --- Position Action CRUD (full, not just append) ---

async def add_position_action(stock_code: str, action_type: str, price: float, shares: int,
                               trade_date: str = None, note: str = "", tranche_id: int = None,
                               fee: float | None = None, trade_time: str | None = None,
                               broker: str | None = None, fx_rate: float | None = None, *, user_id: int) -> int:
    """Insert a new action. Returns the new action id. fx_rate: 港美股成交日汇率(每 1 原币折人民币)。"""
    validate_user_id(user_id)
    db = await get_db()
    try:
        cursor = await db.execute(
            """INSERT INTO position_actions
               (user_id, stock_code, action_type, price, shares, trade_date, note, tranche_id, fee, trade_time, broker,
                fx_rate)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, stock_code, action_type, price, shares, trade_date, note, tranche_id, fee, trade_time, broker,
             fx_rate),
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


async def update_position_action(action_id: int, action_type: str = None, price: float = None,
                                  shares: int = None, trade_date: str = None, note: str = None,
                                  fee: float | None = None, fee_explicit: bool = False,
                                  trade_time: str = None, broker: str = None,
                                  fx_rate: float | None = None, *, user_id: int):
    """Update fields. fee_explicit=True 表示明确想改 fee (即使传 None 也写入 NULL).
    fee 默认 None + fee_explicit=False 不动 fee 列。trade_time/broker 传空串 "" 可清空。
    fx_rate: None 不动; 正数设值; 0 清空(回退按当前汇率)。"""
    validate_user_id(user_id)
    db = await get_db()
    try:
        sets, vals = [], []
        for k, v in [("action_type", action_type), ("price", price), ("shares", shares),
                     ("trade_date", trade_date), ("note", note)]:
            if v is not None:
                sets.append(f"{k} = ?")
                vals.append(v)
        if trade_time is not None:        # "" → 清空(NULL), "HH:MM" → 设值
            sets.append("trade_time = ?")
            vals.append(trade_time or None)
        if broker is not None:            # "" → 清空(回退持仓默认), 券商名 → 设值
            sets.append("broker = ?")
            vals.append(broker or None)
        if fee_explicit:
            sets.append("fee = ?")
            vals.append(fee)
        if fx_rate is not None:
            sets.append("fx_rate = ?")
            vals.append(fx_rate if fx_rate > 0 else None)
        if not sets:
            return
        vals.extend((action_id, user_id))
        cursor = await db.execute(
            f"UPDATE position_actions SET {', '.join(sets)} WHERE id = ? AND user_id = ?", vals
        )
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


async def delete_position_action(action_id: int, *, user_id: int):
    validate_user_id(user_id)
    db = await get_db()
    try:
        cursor = await db.execute('DELETE FROM position_actions WHERE id = ? AND user_id = ?', (action_id, user_id))
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


# --- Morning Briefings ---

async def save_briefing(stock_code: str, briefing_date: str, payload_json: str, *, user_id: int):
    validate_user_id(user_id)
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO morning_briefings (user_id, stock_code, briefing_date, payload_json)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id, stock_code, briefing_date) DO UPDATE SET
                 payload_json = excluded.payload_json,
                 created_at = CURRENT_TIMESTAMP""",
            (user_id, stock_code, briefing_date, payload_json),
        )
        await db.commit()
    finally:
        await db.close()


async def get_briefings_for_date(briefing_date: str, *, user_id: int) -> list[dict]:
    validate_user_id(user_id)
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM morning_briefings WHERE briefing_date = ? AND user_id = ? ORDER BY stock_code",
            (briefing_date, user_id),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def get_latest_briefing(stock_code: str, *, user_id: int) -> dict | None:
    validate_user_id(user_id)
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM morning_briefings WHERE stock_code = ? AND user_id = ? ORDER BY briefing_date DESC LIMIT 1",
            (stock_code, user_id),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


# --- Cashflow Monthly ---

async def upsert_cashflow(month: str, income: float, fixed_cost: float, discretionary: float, notes: str='', *, user_id: int):
    validate_user_id(user_id)
    db = await get_db()
    try:
        await db.execute('INSERT INTO cashflow_monthly (user_id, month, income, fixed_cost, discretionary, notes)\n               VALUES (?, ?, ?, ?, ?, ?)\n               ON CONFLICT(user_id, month) DO UPDATE SET\n                 income = excluded.income,\n                 fixed_cost = excluded.fixed_cost,\n                 discretionary = excluded.discretionary,\n                 notes = excluded.notes,\n                 updated_at = CURRENT_TIMESTAMP', (user_id, month, float(income or 0), float(fixed_cost or 0), float(discretionary or 0), notes or ''))
        await db.commit()
    finally:
        await db.close()


async def get_cashflow(month: str, *, user_id: int) -> dict | None:
    validate_user_id(user_id)
    db = await get_db()
    try:
        cursor = await db.execute('SELECT * FROM cashflow_monthly WHERE user_id = ? AND month = ?', (user_id, month))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def list_cashflow(months: int=12, *, user_id: int) -> list[dict]:
    validate_user_id(user_id)
    db = await get_db()
    try:
        cursor = await db.execute('SELECT * FROM cashflow_monthly WHERE user_id = ? ORDER BY month DESC LIMIT ?', (user_id, max(1, int(months))))
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def delete_cashflow(month: str, *, user_id: int):
    validate_user_id(user_id)
    db = await get_db()
    try:
        await db.execute('DELETE FROM cashflow_monthly WHERE user_id = ? AND month = ?', (user_id, month))
        await db.commit()
    finally:
        await db.close()



# --- External Asset Actions ---

async def list_external_actions(asset_id: int, *, user_id: int) -> list[dict]:
    validate_user_id(user_id)
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM external_asset_actions WHERE asset_id = ? AND user_id = ? ORDER BY trade_date, id",
            (asset_id, user_id),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def add_external_action(asset_id: int, action_type: str, amount: float = 0,
                              shares: float | None = None, unit_price: float | None = None,
                              trade_date: str | None = None, note: str = "",
                              status: str = "confirmed",
                              interest_part: float | None = None,
                              fee: float | None = None,
                              trade_time: str | None = None, *, user_id: int) -> int:
    validate_user_id(user_id)
    db = await get_db()
    try:
        if not await (await db.execute("SELECT 1 FROM external_assets WHERE id=? AND user_id=?", (asset_id, user_id))).fetchone():
            raise ValueError("资产不存在或不属于当前账号")
        cursor = await db.execute(
            """INSERT INTO external_asset_actions
               (user_id, asset_id, action_type, amount, shares, unit_price, trade_date, status, note, interest_part, fee, trade_time)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, asset_id, action_type, float(amount or 0),
             float(shares) if shares is not None else None,
             float(unit_price) if unit_price is not None else None,
             trade_date, status, note or "",
             float(interest_part) if interest_part is not None else None,
             float(fee) if fee is not None else None, trade_time),
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


async def update_external_action(action_id: int, *, user_id: int, **kwargs):
    validate_user_id(user_id)
    if not kwargs:
        return
    if set(kwargs) - {"action_type", "amount", "shares", "unit_price", "trade_date", "status", "note", "interest_part", "fee", "trade_time"}:
        raise ValueError("不允许更新该资产流水字段")
    cols = ", ".join(f"{k} = ?" for k in kwargs.keys())
    db = await get_db()
    try:
        cursor = await db.execute(
            f"UPDATE external_asset_actions SET {cols} WHERE id = ? AND user_id = ?",
            (*kwargs.values(), action_id, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


async def delete_external_action(action_id: int, *, user_id: int):
    validate_user_id(user_id)
    db = await get_db()
    try:
        cursor = await db.execute('DELETE FROM external_asset_actions WHERE id = ? AND user_id = ?', (action_id, user_id))
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


# --- DCA Schedules ---

async def list_dca_schedules(asset_id: int | None = None, *, user_id: int) -> list[dict]:
    validate_user_id(user_id)
    db = await get_db()
    try:
        if asset_id is None:
            cursor = await db.execute('SELECT * FROM dca_schedules WHERE asset_id IN (SELECT id FROM external_assets WHERE user_id = ?) ORDER BY id', (user_id,))
        else:
            cursor = await db.execute(
                "SELECT * FROM dca_schedules WHERE asset_id = ? AND asset_id IN (SELECT id FROM external_assets WHERE user_id = ?) ORDER BY id",
                (asset_id, user_id),
            )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def get_dca_schedule(dca_id: int, *, user_id: int) -> dict | None:
    validate_user_id(user_id)
    db = await get_db()
    try:
        cursor = await db.execute('SELECT * FROM dca_schedules WHERE id = ? AND asset_id IN (SELECT id FROM external_assets WHERE user_id = ?)', (dca_id, user_id))
        row = await cursor.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def add_dca_schedule(asset_id: int, mode: str, value: float,
                           frequency: str = "monthly",
                           day_of_month: int | None = None,
                           day_of_week: int | None = None,
                           next_due: str | None = None, note: str = "", *, user_id: int) -> int:
    # 旧 schema day_of_month 是 NOT NULL, 给 daily/weekly 模式时塞个占位 (1), fire 逻辑会按 frequency 忽略
    dom = int(day_of_month) if day_of_month is not None else 1
    validate_user_id(user_id)
    db = await get_db()
    try:
        if not await (await db.execute("SELECT 1 FROM external_assets WHERE id=? AND user_id=?", (asset_id, user_id))).fetchone():
            raise ValueError("资产不存在或不属于当前账号")
        cursor = await db.execute(
            """INSERT INTO dca_schedules
               (asset_id, mode, value, frequency, day_of_month, day_of_week, next_due, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (asset_id, mode, float(value), frequency, dom,
             int(day_of_week) if day_of_week is not None else None,
             next_due, note or ""),
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


async def update_dca_schedule(dca_id: int, *, user_id: int, **kwargs):
    if set(kwargs) - {"mode", "value", "frequency", "day_of_month", "day_of_week", "status", "next_due", "last_fired_at", "note"}:
        raise ValueError("不允许更新该定投字段")
    if not kwargs:
        return
    cols = ", ".join(f"{k} = ?" for k in kwargs.keys())
    validate_user_id(user_id)
    db = await get_db()
    try:
        await db.execute(
            f"UPDATE dca_schedules SET {cols}, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND asset_id IN (SELECT id FROM external_assets WHERE user_id = ?)",
            (*kwargs.values(), dca_id, user_id),
        )
        await db.commit()
    finally:
        await db.close()


async def delete_dca_schedule(dca_id: int, *, user_id: int):
    validate_user_id(user_id)
    db = await get_db()
    try:
        await db.execute('DELETE FROM dca_schedules WHERE id = ? AND asset_id IN (SELECT id FROM external_assets WHERE user_id = ?)', (dca_id, user_id))
        await db.commit()
    finally:
        await db.close()


async def list_due_dca_schedules(today_str: str, *, user_id: int) -> list[dict]:
    validate_user_id(user_id)
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT * FROM dca_schedules
               WHERE asset_id IN (SELECT id FROM external_assets WHERE user_id = ?) AND status = 'active' AND next_due IS NOT NULL AND next_due <= ?""",
            (user_id, today_str),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


# --- Brokers ---

async def list_brokers(*, user_id: int) -> list[dict]:
    validate_user_id(user_id)
    db = await get_db()
    try:
        cur = await db.execute('SELECT * FROM brokers WHERE user_id = ? ORDER BY is_default DESC, id ASC', (user_id,))
        return [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()


async def get_default_broker(*, user_id: int) -> dict | None:
    validate_user_id(user_id)
    db = await get_db()
    try:
        cur = await db.execute('SELECT * FROM brokers WHERE user_id = ? AND is_default=1 LIMIT 1', (user_id,))
        r = await cur.fetchone()
        if r is None:
            cur = await db.execute('SELECT * FROM brokers WHERE user_id = ? ORDER BY id ASC LIMIT 1', (user_id,))
            r = await cur.fetchone()
        return dict(r) if r else None
    finally:
        await db.close()


async def add_broker(name, stock_rate, stock_min, etf_rate, etf_min, *, user_id: int) -> int:
    validate_user_id(user_id)
    db = await get_db()
    try:
        cur = await db.execute('INSERT INTO brokers (user_id, name, stock_rate, stock_min, etf_rate, etf_min, is_default) VALUES (?, ?,?,?,?,?,0)', (user_id, name, stock_rate, stock_min, etf_rate, etf_min))
        await db.commit()
        return cur.lastrowid
    finally:
        await db.close()


async def update_broker(broker_id: int, *, user_id: int, **kwargs):
    validate_user_id(user_id)
    if set(kwargs) - {'name', 'stock_rate', 'stock_min', 'etf_rate', 'etf_min', 'is_default'}:
        raise ValueError('不允许更新该券商字段')
    if not kwargs:
        return
    db = await get_db()
    try:
        if not await (await db.execute('SELECT 1 FROM brokers WHERE id=? AND user_id=?', (broker_id, user_id))).fetchone():
            return
        if kwargs.get('is_default'):
            await db.execute('UPDATE brokers SET is_default=0 WHERE user_id = ?', (user_id,))
        cols = ', '.join((f'{k}=?' for k in kwargs))
        await db.execute(f'UPDATE brokers SET {cols} WHERE user_id = ? AND id=?', (*kwargs.values(), user_id, broker_id))
        await db.commit()
    finally:
        await db.close()


async def delete_broker(broker_id: int, *, user_id: int):
    validate_user_id(user_id)
    db = await get_db()
    try:
        await db.execute('DELETE FROM brokers WHERE user_id = ? AND id=? AND is_default=0', (user_id, broker_id))
        await db.commit()
    finally:
        await db.close()


# ---- 每日组合市值快照 ----

async def save_portfolio_snapshot(snap_date: str, total_value: float, by_asset_json: str, *, user_id: int):
    validate_user_id(user_id)
    db = await get_db()
    try:
        await db.execute(
            "INSERT OR REPLACE INTO portfolio_snapshots (user_id, snap_date, total_value, by_asset) VALUES (?, ?, ?, ?)",
            (user_id, snap_date, total_value, by_asset_json))
        await db.commit()
    finally:
        await db.close()


async def list_portfolio_snapshots(limit: int = 500, *, user_id: int) -> list[dict]:
    validate_user_id(user_id)
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT snap_date, total_value, by_asset FROM portfolio_snapshots WHERE user_id = ? ORDER BY snap_date DESC LIMIT ?",
            (user_id, limit))
        rows = await cur.fetchall()
        return [{"snap_date": r[0], "total_value": r[1], "by_asset": r[2]} for r in rows]
    finally:
        await db.close()


# ---- 板块成交额份额档案 ----

async def save_sector_shares(snap_date: str, rows: list[dict]):
    db = await get_db()
    try:
        await db.executemany(
            "INSERT OR REPLACE INTO sector_share_history (snap_date, board, amount_yi, share_pct) VALUES (?, ?, ?, ?)",
            [(snap_date, r["board"], r["amount_yi"], r["share_pct"]) for r in rows])
        await db.commit()
    finally:
        await db.close()


async def get_sector_shares_on(snap_date: str) -> dict:
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT board, share_pct FROM sector_share_history WHERE snap_date = ?", (snap_date,))
        return {r[0]: r[1] for r in await cur.fetchall()}
    finally:
        await db.close()


async def save_market_volume_history(market: str, rows: list):
    """rows: [(YYYY-MM-DD, vol股, amt元)], INSERT OR REPLACE 幂等回填。"""
    if not rows:
        return
    db = await get_db()
    try:
        await db.executemany(
            "INSERT OR REPLACE INTO market_volume_history (snap_date, market, vol, amt) VALUES (?, ?, ?, ?)",
            [(d, market, v, a) for d, v, a in rows])
        await db.commit()
    finally:
        await db.close()


async def get_market_volume_history(markets: list, days: int = 20) -> dict:
    """→ {market: [(date, amt元)]} 升序, 近 days 个档案日。"""
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT snap_date, market, amt FROM market_volume_history "
            "WHERE market IN (%s) ORDER BY snap_date DESC LIMIT ?" % ",".join("?" * len(markets)),
            (*markets, days * len(markets)))
        out: dict = {m: [] for m in markets}
        for d, m, a in await cur.fetchall():
            out[m].append((d, a))
        for m in out:
            out[m].reverse()
        return out
    finally:
        await db.close()


async def list_sector_share_dates(limit: int = 20) -> list[str]:
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT DISTINCT snap_date FROM sector_share_history ORDER BY snap_date DESC LIMIT ?", (limit,))
        return [r[0] for r in await cur.fetchall()]
    finally:
        await db.close()


# ---- 自选观察池 ----

async def add_watchlist(code: str, name: str, price: float | None, *, user_id: int):
    validate_user_id(user_id)
    import datetime
    db = await get_db()
    try:
        await db.execute('INSERT OR REPLACE INTO watchlist (user_id, stock_code, stock_name, added_at, added_price) VALUES (?, ?, ?, ?, ?)', (user_id, code, name, datetime.date.today().isoformat(), price))
        cur = await db.execute('SELECT COALESCE(MAX(group_order),0)+1 FROM watchlist_group WHERE user_id = ? AND grp=?', (user_id, DEFAULT_WATCH_GROUP))
        nxt = (await cur.fetchone())[0] or 0
        await db.execute('INSERT OR IGNORE INTO watchlist_group (user_id, stock_code, grp, group_order) VALUES (?, ?,?,?)', (user_id, code, DEFAULT_WATCH_GROUP, nxt))
        await db.commit()
    finally:
        await db.close()


async def remove_watchlist(code: str, *, user_id: int):
    validate_user_id(user_id)
    db = await get_db()
    try:
        await db.execute('DELETE FROM watchlist WHERE user_id = ? AND stock_code = ?', (user_id, code))
        await db.execute('DELETE FROM watchlist_group WHERE user_id = ? AND stock_code = ?', (user_id, code))
        await db.commit()
    finally:
        await db.close()


async def list_watchlist(*, user_id: int) -> list[dict]:
    """自选列表。按全局位次排(位次相同则退回加入时间倒序, 与改造前一致)。
    分组不参与全局排序 —— 一只票可以在好几个组里, 「全部」视图只有一种顺序。
    groups=所属分组名(空列表=还没归组, 即默认组「自选」);
    group_orders={分组名: 组内位次}, 前端选中某个组时按它排。"""
    validate_user_id(user_id)
    db = await get_db()
    try:
        cur = await db.execute('SELECT stock_code, stock_name, added_at, added_price, COALESCE(sort_order,0) FROM watchlist WHERE user_id = ? ORDER BY COALESCE(sort_order,0), created_at DESC', (user_id,))
        rows = await cur.fetchall()
        cur = await db.execute('SELECT stock_code, grp, COALESCE(group_order,0) FROM watchlist_group WHERE user_id = ? ORDER BY grp', (user_id,))
        gm: dict[str, list[str]] = {}
        om: dict[str, dict[str, float]] = {}
        for code, grp, order in await cur.fetchall():
            gm.setdefault(code, []).append(grp)
            om.setdefault(code, {})[grp] = order or 0
        return [{'code': r[0], 'name': r[1], 'added_at': r[2], 'added_price': r[3], 'sort_order': r[4] or 0, 'groups': gm.get(r[0], []), 'group_orders': om.get(r[0], {})} for r in rows]
    finally:
        await db.close()


async def set_watchlist_groups(code: str, groups: list[str], *, user_id: int) -> list[str]:
    """整套覆盖这只票的分组(前端本地勾完提交全集, 幂等)。返回落库后的分组表。

    已经在的组保留原位次 —— 重写位次的话勾一个新组会把这只票在老组里挪到末尾。
    新进的组排到该组末尾。全局位次 sort_order 一概不动: 在「全部」视图里改分组标签
    不该让这只票凭空跳位置。
    传空列表 = 退回默认组「自选」(而不是一个组都不在: 那样这只票只能在「全部」里看到)。
    """
    validate_user_id(user_id)
    want = []
    for g in groups or []:
        g = (str(g) or '').strip()
        if g and g not in want:
            want.append(g)
    if not want:
        want = [DEFAULT_WATCH_GROUP]
    db = await get_db()
    try:
        if not await (await db.execute('SELECT 1 FROM watchlist WHERE user_id=? AND stock_code=?', (user_id, code))).fetchone():
            return []
        cur = await db.execute('SELECT grp FROM watchlist_group WHERE user_id = ? AND stock_code=?', (user_id, code))
        have = {r[0] for r in await cur.fetchall()}
        for g in have - set(want):
            await db.execute('DELETE FROM watchlist_group WHERE user_id = ? AND stock_code=? AND grp=?', (user_id, code, g))
        for g in want:
            if g in have:
                continue
            cur = await db.execute('SELECT COALESCE(MAX(group_order),0)+1 FROM watchlist_group WHERE user_id = ? AND grp=?', (user_id, g))
            nxt = (await cur.fetchone())[0] or 0
            await db.execute('INSERT OR IGNORE INTO watchlist_group (user_id, stock_code, grp, group_order) VALUES (?, ?,?,?)', (user_id, code, g, nxt))
        await db.commit()
        return want
    finally:
        await db.close()


async def reorder_watchlist(codes: list[str], scope: str='global', group: str | None=None, *, user_id: int) -> None:
    """按给定顺序覆盖写位次。

    scope='global': 写 sort_order(「全部」视图拖动), 不碰分组;
    scope='group' : 写这些票在 group 里的位次, 顺带把还不在该组的加进去(拖进组=入组)。
                    只动这一个组的成员关系, 票在别的组里待着不受影响。
    两套位次独立, 所以在组内调顺序不会打乱「全部」视图, 反之亦然。
    """
    validate_user_id(user_id)
    db = await get_db()
    try:
        for i, c in enumerate(codes or []):
            if not await (await db.execute('SELECT 1 FROM watchlist WHERE user_id=? AND stock_code=?', (user_id, c))).fetchone():
                continue
            if scope == 'group':
                if not (group or ''):
                    continue
                await db.execute('INSERT INTO watchlist_group (user_id, stock_code, grp, group_order) VALUES (?, ?,?,?) ON CONFLICT(user_id, stock_code, grp) DO UPDATE SET group_order=excluded.group_order', (user_id, c, group, float(i)))
            else:
                await db.execute('UPDATE watchlist SET sort_order=? WHERE user_id = ? AND stock_code=?', (float(i), user_id, c))
        await db.commit()
    finally:
        await db.close()


# ---- 全市场代码↔名称字典(搜股底表, 落盘避免每次重启重拉 20s+) ----

async def save_symbol_dict(rows: list[tuple]) -> int:
    """rows: [(code, name, is_etf)]。整表覆盖式 upsert, 不删旧码(退市票留着仍可搜历史)。"""
    if not rows:
        return 0
    db = await get_db()
    try:
        await db.executemany(
            "INSERT INTO symbol_dict (code, name, is_etf, updated_at) VALUES (?,?,?,CURRENT_TIMESTAMP) "
            "ON CONFLICT(code) DO UPDATE SET name=excluded.name, is_etf=excluded.is_etf, "
            "updated_at=CURRENT_TIMESTAMP",
            [(str(c), str(n), int(e)) for c, n, e in rows])
        await db.commit()
        return len(rows)
    finally:
        await db.close()


async def load_symbol_dict() -> tuple[list[tuple], str | None]:
    """→ ([(code, name, is_etf)], 最新更新时间)。空表返回 ([], None)。"""
    db = await get_db()
    try:
        cur = await db.execute('SELECT code, name, COALESCE(is_etf,0) FROM symbol_dict')
        rows = [(r[0], r[1], int(r[2])) for r in await cur.fetchall()]
        cur = await db.execute('SELECT MAX(updated_at) FROM symbol_dict')
        ts = (await cur.fetchone())[0]
        return rows, ts
    finally:
        await db.close()


# ---- 市场情绪逐日档案 ----

async def save_sentiment_day(row: dict):
    db = await get_db()
    try:
        await db.execute(
            "INSERT OR REPLACE INTO sentiment_history (snap_date, n_zt, n_dt, n_zb, zbl_rate, max_lb, money_effect)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (row["date"], row.get("n_zt"), row.get("n_dt"), row.get("n_zb"),
             row.get("zbl_rate"), row.get("max_lb"), row.get("money_effect")))
        await db.commit()
    finally:
        await db.close()


async def list_sentiment_history(limit: int = 60) -> list[dict]:
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT snap_date, n_zt, n_dt, n_zb, zbl_rate, max_lb, money_effect"
            " FROM sentiment_history ORDER BY snap_date DESC LIMIT ?", (limit,))
        rows = await cur.fetchall()
        return [{"date": r[0], "n_zt": r[1], "n_dt": r[2], "n_zb": r[3],
                 "zbl_rate": r[4], "max_lb": r[5], "money_effect": r[6]} for r in rows]
    finally:
        await db.close()


_LUP_COLS = ("snap_date", "stock_code", "name", "seal_amount", "first_seal", "last_seal",
             "lb_count", "broken_times", "zt_days", "zt_ct", "industry", "theme",
             "amount", "float_mv", "turnover", "pct", "source")


async def save_limit_up_pool(rows: list[dict]) -> int:
    """写入逐只涨停档案。

    **东财可以盖扩展源, 扩展源不许盖东财** —— 东财那份多了炸板次数/换手/流通市值/N天M板,
    历史回填要是后跑就会把这些列刷成空。所以冲突时只在「同源」或「来源是 em」时才更新。
    """
    if not rows:
        return 0
    db = await get_db()
    try:
        n = 0
        for r in rows:
            cur = await db.execute(
                f"INSERT INTO limit_up_pool ({','.join(_LUP_COLS)})"
                f" VALUES ({','.join('?' * len(_LUP_COLS))})"
                " ON CONFLICT(snap_date, stock_code) DO UPDATE SET"
                "   name=excluded.name, seal_amount=excluded.seal_amount,"
                "   first_seal=excluded.first_seal, last_seal=excluded.last_seal,"
                "   lb_count=excluded.lb_count, broken_times=excluded.broken_times,"
                "   zt_days=excluded.zt_days, zt_ct=excluded.zt_ct,"
                "   industry=excluded.industry, theme=excluded.theme, amount=excluded.amount,"
                "   float_mv=excluded.float_mv, turnover=excluded.turnover, pct=excluded.pct,"
                "   source=excluded.source"
                " WHERE excluded.source = 'em' OR limit_up_pool.source = excluded.source",
                tuple(r.get(c) for c in _LUP_COLS))
            n += cur.rowcount or 0
        await db.commit()
        return n
    finally:
        await db.close()


async def get_limit_up_pool(day: str, *, user_id: int | None = None) -> list[dict]:
    db = await get_db()
    try:
        cur = await db.execute(
            f"SELECT {','.join(_LUP_COLS)} FROM limit_up_pool WHERE snap_date = ?"
            " ORDER BY seal_amount DESC", (day,))
        rows = [dict(zip(_LUP_COLS, r)) for r in await cur.fetchall()]
        if user_id is not None:
            import json
            private = json.loads(await get_user_setting(f'provider_pool:{day}',user_id=user_id) or '[]')
            merged = {r['stock_code']:r for r in rows}
            for row in private:
                merged[row['stock_code']] = row
            rows = list(merged.values())
        return rows
    finally:
        await db.close()


async def list_limit_up_days(limit: int = 60, end: str | None = None, *, user_id: int | None = None) -> list[dict]:
    """逐日汇总: 只数 / 封单合计 / 最高连板。序列用, 不拉明细。"""
    db = await get_db()
    try:
        sql = ("SELECT snap_date, COUNT(*), SUM(seal_amount), MAX(lb_count)"
               " FROM limit_up_pool")
        args: tuple = ()
        if end:
            sql += " WHERE snap_date <= ?"
            args = (end,)
        sql += " GROUP BY snap_date ORDER BY snap_date DESC LIMIT ?"
        cur = await db.execute(sql, args + (limit,))
        rows = await cur.fetchall()
        result = [{"date": r[0], "n": r[1], "seal_sum": r[2] or 0, "max_lb": r[3]}
                for r in reversed(rows)]
        if user_id is not None:
            validate_user_id(user_id)
            import json
            private = await (await db.execute("SELECT key,value FROM user_settings WHERE user_id=? AND key LIKE 'provider_pool:%'", (user_id,))).fetchall()
            merged = {r['date']:r for r in result}
            for record in private:
                day = record['key'].split(':',1)[1]
                if end and day > end:
                    continue
                values = json.loads(record['value'])
                merged[day] = {'date':day,'n':len(values),'seal_sum':sum(r.get('seal_amount') or 0 for r in values),'max_lb':max((r.get('lb_count') or 0 for r in values),default=0)}
            result = sorted(merged.values(),key=lambda r:r['date'])[-limit:]
        return result
    finally:
        await db.close()


async def get_next_bars(codes: list[str], day: str) -> dict[str, dict]:
    """每只票在 day **之后**第一根日线 → {代码: {date, open, close}}。

    封单额兑现度回测用: 涨停发生在 day, 要看的是次日开盘接不接、收盘守不守得住。
    严格大于 day, 所以停牌一天就顺延到复牌那根 —— 调用方按返回的 date 自己决定要不要采信。
    """
    if not codes:
        return {}
    db = await get_db()
    try:
        out: dict[str, dict] = {}
        for i in range(0, len(codes), 400):
            chunk = codes[i:i + 400]
            q = ",".join("?" * len(chunk))
            cur = await db.execute(
                f"SELECT stock_code, date, open, close FROM kline_cache WHERE stock_code IN ({q})"
                " AND date > ? ORDER BY stock_code, date", (*chunk, day))
            for code, d, o, c in await cur.fetchall():
                if code not in out:          # 已按 date 升序, 第一条即次日
                    out[code] = {"date": d, "open": o, "close": c}
        return out
    finally:
        await db.close()


_TAG_COLS = ("snap_date", "stock_code", "name", "lb_count", "jj_days", "jj_boards",
             "jj_partial", "amt_rank", "amt", "theme", "industry")


async def save_stock_tags(rows: list[dict]) -> int:
    """写归类层。整行覆盖(归类是纯派生的, 重跑就该得到同一结果)。"""
    if not rows:
        return 0
    db = await get_db()
    try:
        await db.executemany(
            f"INSERT OR REPLACE INTO stock_tag ({','.join(_TAG_COLS)})"
            f" VALUES ({','.join('?' * len(_TAG_COLS))})",
            [tuple(r.get(c) for c in _TAG_COLS) for r in rows])
        await db.commit()
        return len(rows)
    finally:
        await db.close()


async def get_stock_tags(day: str) -> list[dict]:
    db = await get_db()
    try:
        cur = await db.execute(
            f"SELECT {','.join(_TAG_COLS)} FROM stock_tag WHERE snap_date = ?"
            " ORDER BY lb_count DESC, amt_rank IS NULL, amt_rank", (day,))
        return [dict(zip(_TAG_COLS, r)) for r in await cur.fetchall()]
    finally:
        await db.close()


async def get_tag_dates(limit: int = 400) -> list[str]:
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT DISTINCT snap_date FROM stock_tag ORDER BY snap_date DESC LIMIT ?", (limit,))
        return [r[0] for r in reversed(await cur.fetchall())]
    finally:
        await db.close()


async def stock_tag_coverage() -> dict:
    """各轴各自覆盖多少天 —— 钱轴天生比板轴少, 报出来别让人以为一样齐。"""
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT COUNT(DISTINCT snap_date), COUNT(*),"
            " SUM(CASE WHEN lb_count IS NOT NULL THEN 1 ELSE 0 END),"
            " SUM(CASE WHEN jj_boards IS NOT NULL THEN 1 ELSE 0 END),"
            " SUM(CASE WHEN jj_partial = 1 THEN 1 ELSE 0 END),"
            " SUM(CASE WHEN amt_rank IS NOT NULL THEN 1 ELSE 0 END),"
            " SUM(CASE WHEN theme IS NOT NULL OR industry IS NOT NULL THEN 1 ELSE 0 END),"
            " MIN(snap_date), MAX(snap_date) FROM stock_tag")
        d, n, lb, jj, part, amt, th, lo, hi = await cur.fetchone()
        cur = await db.execute(
            "SELECT COUNT(DISTINCT snap_date) FROM stock_tag WHERE amt_rank IS NOT NULL")
        amt_days = (await cur.fetchone())[0]
        return {"days": d or 0, "rows": n or 0, "first": lo, "last": hi,
                "板": lb or 0, "进": jj or 0, "进_窗口不全": part or 0,
                "钱": amt or 0, "钱_天数": amt_days or 0, "题": th or 0}
    finally:
        await db.close()


async def pool_codes_missing_klines() -> tuple[list[str], str]:
    """涨停档案里**自己上榜那几天缺日线**的代码, 以及档案起点日期。

    判据故意不是"够不够 N 根": 试过 >=200 根这版, 结果 55 只新股被判成永远缺 ——
    001232 是 2026-08-04 上市的, 一共才 24 根, 到不了 200, 于是每天重抓一遍,
    而且新库回填时 remaining 永远归不了零。可它对我们的用途一点都不缺: 上榜 1 次、
    那天的日线就在。

    分池兑现率真正要的是"每个 (上榜日, 代码) 都能取到当天的收盘价"(次日那根由
    get_next_bars 自己找)。所以判据就照这个写, 顺带自然终止 —— 补上了就不再进名单。
    """
    db = await get_db()
    try:
        cur = await db.execute('SELECT MIN(snap_date) FROM limit_up_pool')
        since = (await cur.fetchone())[0]
        if not since:
            return [], ""
        cur = await db.execute(
            "SELECT DISTINCT p.stock_code FROM limit_up_pool p"
            " WHERE NOT EXISTS (SELECT 1 FROM kline_cache k"
            "   WHERE k.stock_code = p.stock_code AND k.date = p.snap_date)"
            " ORDER BY p.stock_code")
        return [r[0] for r in await cur.fetchall()], since
    finally:
        await db.close()


async def limit_up_pool_coverage() -> dict:
    """回填进度/来源构成。凭空说"有历史"没用, 要能报出到底攒了多少天。"""
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT COUNT(DISTINCT snap_date), COUNT(*), MIN(snap_date), MAX(snap_date)"
            " FROM limit_up_pool")
        days, rows, lo, hi = await cur.fetchone()
        cur = await db.execute(
            "SELECT source, COUNT(DISTINCT snap_date) FROM limit_up_pool GROUP BY source")
        by_src = {r[0]: r[1] for r in await cur.fetchall()}
        return {"days": days or 0, "rows": rows or 0, "first": lo, "last": hi,
                "days_by_source": by_src}
    finally:
        await db.close()


PUBLIC_SETTING_KEYS = {'public_close_date','network_proxy','tdx_base_url','market_provider','watchlist_default_group_filled', 'llm_base_url','llm_api_key','llm_api_key_header','llm_api_key_prefix','llm_proxy','llm_proxy_url','llm_model_map'}


async def get_user_setting(key: str, *, user_id: int) -> str | None:
    validate_user_id(user_id)
    db = await get_db()
    try:
        row = await (await db.execute('SELECT value FROM user_settings WHERE user_id=? AND key=?', (user_id, key))).fetchone()
        return row['value'] if row else None
    finally:
        await db.close()


async def set_user_setting(key: str, value: str, *, user_id: int):
    validate_user_id(user_id)
    db = await get_db()
    try:
        await db.execute('INSERT INTO user_settings(user_id,key,value) VALUES (?,?,?) ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value', (user_id, key, value))
        await db.commit()
    finally:
        await db.close()
