#!/usr/bin/env python3
"""
Binance Alpha Monitor v2 — 稳定版
REST轮询 + 智能过滤 + 评级 + TG推送
零API Key，零AI成本（评级用规则引擎）

运行: python3 alpha_monitor.py
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import httpx

# ============================================================
# 配置
# ============================================================

BASE_DIR = Path(__file__).parent
DB_PATH = str(BASE_DIR / "data" / "alpha.db")

# TG
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")

# LLM (可选，用于抽取叙事，不配置则降级为规则)
# 优先从hermes auth.json读credential pool（与scanner一致）
def _load_anthropic_key():
    """从auth.json或环境变量获取API key"""
    # 1. hermes auth.json credential pool
    try:
        auth_file = os.path.expanduser("~/.hermes/auth.json")
        if os.path.exists(auth_file):
            with open(auth_file) as f:
                auth = json.load(f)
            pool = auth.get("credential_pool", {}).get("anthropic", [])
            if pool:
                key = pool[0].get("access_token", "")
                if key and key != "***":
                    return key
    except Exception:
        pass
    # 2. 环境变量 fallback
    return os.environ.get("ANTHROPIC_API_KEY", "")

ANTHROPIC_API_KEY = _load_anthropic_key()
ANTHROPIC_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

# 轮询间隔
ANNOUNCEMENT_POLL_INTERVAL = 30   # 公告轮询30秒
AGGREGATION_POLL_INTERVAL = 15    # 聚合工作者15秒
MONITOR_POLL_INTERVAL = 120       # 上线后监控2分钟

# HTTP
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}

BINANCE_ANNOUNCEMENT_API = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"

# ============================================================
# 日志
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("alpha")

# ============================================================
# 过滤规则
# ============================================================

# 触发关键词 — 命中任意一个就触发
TRIGGER_KEYWORDS = [
    "alpha", "空投", "airdrop", "tge", "token generation",
    "将上线", "will list", "will launch",
    "独家", "exclusive", "binance wallet", "hodler",
]

# 排除关键词 — 命中任意一个直接排除
EXCLUDE_KEYWORDS = [
    "delisting", "delist", "下架", "deprecate", "退市",
    "maintenance", "维护",
    "launchpool", "megadrop",
    "buyback", "回购",
    "已完成", "完成结算",
    "perpetual contract",       # 永续合约
    "futures will launch",      # 期货上新
    "usdⓢ-margined",           # U本位合约
    "coin-margined",            # 币本位合约
    "margin will add",          # 杠杆上新
    "trading bots services",    # 交易机器人
    "trading pairs",            # 交易对调整(非新币)
]

# Alpha Box 盲盒
ALPHA_BOX_KEYWORDS = ["alpha box", "盲盒", "mystery box"]

# ============================================================
# VC 白名单
# ============================================================

TIER1_VCS = [
    "binance labs", "yzi labs",
    "coinbase ventures", "a16z", "andreessen horowitz", "paradigm",
    "polychain", "polychain capital", "sequoia", "sequoia china", "sequoia capital",
    "multicoin", "multicoin capital", "pantera", "pantera capital",
    "dragonfly", "dragonfly capital", "founders fund",
]

TIER2_VCS = [
    "abcde", "iosg", "hashkey", "okx ventures",
    "sevenx", "folius", "foresight", "hashed",
    "bitkraft", "framework", "framework ventures",
    "delphi", "delphi digital", "electric capital",
    "variant", "1kx", "placeholder",
    "animoca", "animoca brands", "jump", "jump crypto",
    "hack vc", "bain capital",
]

# 叙事热度
HOT_NARRATIVES = ["defi_perp", "ai_agent", "ai_defi", "defai", "zk_proof"]
WEAK_NARRATIVES = ["gamefi", "meme", "social"]

# 币安亲儿子信号
BINANCE_DARLING_KEYWORDS = ["yzi labs", "binance labs"]

# ============================================================
# 评级引擎
# ============================================================

TIER_ICONS = {"S": "🟢🟢🟢", "A": "🟡🟡", "B": "🟠", "C": "⚪"}
TIER_LABELS = {"S": "S 级(必研究)", "A": "A 级(值得看)", "B": "B 级(正常)", "C": "C 级(了解)"}


def count_vc_tier(vcs: list, vc_list: list) -> int:
    count = 0
    vcs_lower = [v.lower() for v in vcs]
    for t in vc_list:
        if any(t in v for v in vcs_lower):
            count += 1
    return count


def rate_project(circ_mcap: float, fdv: float, vcs: list,
                 narrative: str, is_darling: bool) -> dict:
    """S/A/B/C 评级"""
    t1 = count_vc_tier(vcs, TIER1_VCS)
    t2 = count_vc_tier(vcs, TIER2_VCS)
    hot = narrative in HOT_NARRATIVES
    weak = narrative in WEAK_NARRATIVES
    circ_mcap = circ_mcap or 0
    fdv = fdv or 0

    warnings = []
    if weak:
        warnings.append(f"⚠️ {narrative} 历史破发率较高")

    # S级 5条路径
    if is_darling:
        return {"tier": "S", "reason": "币安亲儿子(YZi/Binance Labs/CZ)", "warnings": warnings}
    if hot and t1 >= 1 and fdv < 500_000_000:
        return {"tier": "S", "reason": f"热叙事({narrative})+ Tier1 VC", "warnings": warnings}
    if t1 >= 2 and circ_mcap < 50_000_000 and fdv < 300_000_000:
        return {"tier": "S", "reason": "≥2家 Tier1 中盘", "warnings": warnings}
    if t1 >= 1 and circ_mcap < 10_000_000 and fdv < 100_000_000:
        return {"tier": "S", "reason": "Tier1 微盘", "warnings": warnings}
    if hot and circ_mcap < 10_000_000 and fdv < 50_000_000:
        return {"tier": "S", "reason": f"热叙事({narrative})微盘", "warnings": warnings}

    # A级
    if t1 >= 1 and circ_mcap < 20_000_000 and fdv < 200_000_000:
        return {"tier": "A", "reason": "Tier1 小盘", "warnings": warnings}

    # B级
    if circ_mcap < 50_000_000 and fdv < 500_000_000:
        return {"tier": "B", "reason": "中盘", "warnings": warnings}

    return {"tier": "C", "reason": "大盘/弱信号", "warnings": warnings}


# ============================================================
# 数据库
# ============================================================

def init_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS projects (
        id TEXT PRIMARY KEY,
        symbol TEXT NOT NULL,
        name TEXT,
        launch_time TEXT,
        source TEXT,
        raw_text TEXT,
        tier TEXT DEFAULT 'PENDING',
        tier_reason TEXT,
        narrative TEXT,
        narrative_desc TEXT,
        vcs_json TEXT DEFAULT '[]',
        is_darling INTEGER DEFAULT 0,
        open_price REAL,
        total_supply REAL,
        circulating_supply REAL,
        fdv REAL,
        circulating_mcap REAL,
        excluded INTEGER DEFAULT 0,
        exclude_reason TEXT,
        discovered_at TEXT,
        updated_at TEXT
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS pushes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL,
        push_type TEXT,
        sent_at TEXT,
        content TEXT
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        price REAL,
        circulating_mcap REAL,
        fdv REAL
    )""")
    conn.commit()
    conn.close()


def project_id(symbol: str, date_str: str) -> str:
    return hashlib.md5(f"{symbol.upper()}_{date_str}".encode()).hexdigest()[:16]


def project_exists(pid: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    exists = conn.execute("SELECT 1 FROM projects WHERE id=?", (pid,)).fetchone() is not None
    conn.close()
    return exists


def save_project(project: dict):
    conn = sqlite3.connect(DB_PATH)
    now = datetime.utcnow().isoformat()
    conn.execute("""
    INSERT OR IGNORE INTO projects
    (id, symbol, name, launch_time, source, raw_text, tier, tier_reason,
     narrative, narrative_desc, vcs_json, is_darling,
     open_price, total_supply, circulating_supply, fdv, circulating_mcap,
     excluded, exclude_reason, discovered_at, updated_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        project["id"], project["symbol"], project.get("name"),
        project.get("launch_time"), project.get("source"), project.get("raw_text"),
        project.get("tier", "PENDING"), project.get("tier_reason"),
        project.get("narrative"), project.get("narrative_desc"),
        json.dumps(project.get("vcs", [])), int(project.get("is_darling", False)),
        project.get("open_price"), project.get("total_supply"),
        project.get("circulating_supply"), project.get("fdv"),
        project.get("circulating_mcap"),
        int(project.get("excluded", 0)), project.get("exclude_reason"),
        now, now,
    ))
    conn.commit()
    conn.close()


def update_project(pid: str, fields: dict):
    if not fields:
        return
    conn = sqlite3.connect(DB_PATH)
    fields["updated_at"] = datetime.utcnow().isoformat()
    set_parts = [f"{k}=?" for k in fields]
    values = list(fields.values()) + [pid]
    conn.execute(f"UPDATE projects SET {','.join(set_parts)} WHERE id=?", values)
    conn.commit()
    conn.close()


def get_project(pid: str) -> Optional[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_pending() -> list:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM projects WHERE excluded=0 AND tier='PENDING' ORDER BY discovered_at"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_active() -> list:
    """上线后需要监控的项目（非PENDING非EXCLUDED，有launch_time）"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT * FROM projects
        WHERE excluded=0 AND launch_time IS NOT NULL AND launch_time != ''
        AND tier NOT IN ('PENDING', 'EXCLUDED', 'ERROR')
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def has_pushed(pid: str, push_type: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    exists = conn.execute(
        "SELECT 1 FROM pushes WHERE project_id=? AND push_type=?", (pid, push_type)
    ).fetchone() is not None
    conn.close()
    return exists


def log_push(pid: str, push_type: str, content: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO pushes (project_id, push_type, sent_at, content) VALUES (?,?,?,?)",
        (pid, push_type, datetime.utcnow().isoformat(), content)
    )
    conn.commit()
    conn.close()


def save_snapshot(pid: str, price: float, mcap: float, fdv: float):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO snapshots (project_id, timestamp, price, circulating_mcap, fdv) VALUES (?,?,?,?,?)",
        (pid, datetime.utcnow().isoformat(), price, mcap, fdv)
    )
    conn.commit()
    conn.close()


# ============================================================
# TG 推送
# ============================================================

async def send_tg(text: str, silent: bool = False) -> bool:
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        logger.error("TG_BOT_TOKEN 或 TG_CHAT_ID 未配置")
        return False
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_notification": silent,
    }
    try:
        async with httpx.AsyncClient(timeout=15, headers=HEADERS) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                logger.error(f"TG发送失败 {resp.status_code}: {resp.text[:200]}")
                return False
            return True
    except Exception as e:
        logger.error(f"TG发送异常: {e}")
        return False


# ============================================================
# 公告标题解析
# ============================================================

def is_trigger(title: str) -> tuple[bool, Optional[str]]:
    t = title.lower()
    for kw in EXCLUDE_KEYWORDS:
        if kw.lower() in t:
            return False, f"排除: {kw}"
    for kw in ALPHA_BOX_KEYWORDS:
        if kw.lower() in t:
            return False, "Alpha Box 盲盒"
    for kw in TRIGGER_KEYWORDS:
        if kw.lower() in t:
            return True, None
    return False, None


def extract_symbol(title: str) -> Optional[str]:
    # 英文括号: "Chip (CHIP)"
    m = re.search(r"\(([A-Z0-9]{2,10})\)", title)
    if m:
        return m.group(1)
    # 中文括号
    m = re.search(r"（([A-Z0-9]{2,10})）", title)
    if m:
        return m.group(1)
    return None


def extract_name(title: str) -> Optional[str]:
    patterns = [
        r"(?:上线|List|list|Launch|launch|featured)\s+([A-Za-z0-9 ]+?)\s*[\(（]",
    ]
    for p in patterns:
        m = re.search(p, title, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return None


# ============================================================
# 币安公告抓取
# ============================================================

async def fetch_announcements(limit: int = 20) -> list:
    """抓取币安最新公告"""
    all_articles = []
    # 48: New Cryptocurrency Listing, 161: Latest Activities, 93: Latest News
    for catalog_id in [48, 161, 93]:
        params = {"type": 1, "catalogId": catalog_id, "pageNo": 1, "pageSize": limit}
        try:
            async with httpx.AsyncClient(timeout=15, headers=HEADERS) as client:
                resp = await client.get(BINANCE_ANNOUNCEMENT_API, params=params)
                resp.raise_for_status()
                data = resp.json()
                for catalog in data.get("data", {}).get("catalogs", []):
                    for a in catalog.get("articles", []):
                        a["_catalog_id"] = catalog_id
                        all_articles.append(a)
        except Exception as e:
            logger.warning(f"抓取分类 {catalog_id} 失败: {e}")

    # 去重
    seen = set()
    unique = []
    for a in all_articles:
        code = a.get("code")
        if code and code not in seen:
            seen.add(code)
            unique.append(a)
    return unique


# ============================================================
# CoinGecko 数据
# ============================================================

async def fetch_coingecko(symbol: str, project_name: str = "") -> dict:
    """查CoinGecko代币经济数据。双重匹配：symbol精确匹配 + 项目名模糊匹配"""
    result = {"found": False, "price": None, "fdv": None, "mcap": None,
              "total_supply": None, "circ_supply": None, "chain": None, "contract": None}
    try:
        async with httpx.AsyncClient(timeout=15, headers=HEADERS) as client:
            # 搜索策略：先搜symbol，如果项目名不同再搜项目名
            queries = [symbol]
            if project_name and project_name.upper() != symbol.upper():
                queries.append(project_name)

            coin_id = None
            best_rank = 999999
            name_exact_match = None  # 项目名精确匹配优先

            for query in queries:
                resp = await client.get("https://api.coingecko.com/api/v3/search",
                                        params={"query": query})
                if resp.status_code != 200:
                    continue
                coins = resp.json().get("coins", [])

                for c in coins:
                    c_sym = c.get("symbol", "").upper()
                    c_name = c.get("name", "").lower()
                    c_rank = c.get("market_cap_rank") or 999999

                    # 项目名精确匹配（最高优先级，如MegaETH -> megaeth）
                    if project_name and c_name == project_name.lower():
                        name_exact_match = c["id"]

                    # 精确symbol匹配 — 取market_cap_rank最高（最小）的
                    if c_sym == symbol.upper():
                        if c_rank < best_rank:
                            coin_id = c["id"]
                            best_rank = c_rank

                    # 项目名模糊匹配（如MegaETH搜mega）
                    if project_name and project_name.lower() in c_name:
                        if c_rank < best_rank:
                            coin_id = c["id"]
                            best_rank = c_rank

            # 项目名精确匹配 > 其他匹配
            if name_exact_match:
                coin_id = name_exact_match

            if not coin_id:
                logger.info(f"CoinGecko未找到 {symbol}/{project_name}")
                return result

            logger.info(f"CoinGecko匹配: {symbol} -> {coin_id} (rank={best_rank})")

            resp2 = await client.get(
                f"https://api.coingecko.com/api/v3/coins/{coin_id}",
                params={"localization": "false", "tickers": "false",
                        "market_data": "true", "community_data": "false",
                        "developer_data": "false"}
            )
            if resp2.status_code == 429:
                await asyncio.sleep(5)
                resp2 = await client.get(
                    f"https://api.coingecko.com/api/v3/coins/{coin_id}",
                    params={"localization": "false", "tickers": "false",
                            "market_data": "true", "community_data": "false",
                            "developer_data": "false"}
                )
            if resp2.status_code != 200:
                return result
            d = resp2.json()
            md = d.get("market_data", {})
            result.update({
                "found": True,
                "price": (md.get("current_price") or {}).get("usd"),
                "fdv": (md.get("fully_diluted_valuation") or {}).get("usd"),
                "mcap": (md.get("market_cap") or {}).get("usd"),
                "total_supply": md.get("total_supply"),
                "circ_supply": md.get("circulating_supply"),
            })
            # 提取categories（含VC信息如"YZi Labs Portfolio"）和description
            result["categories"] = d.get("categories", [])
            result["description"] = (d.get("description") or {}).get("en", "")[:500]
            platforms = d.get("platforms", {})
            for chain, addr in platforms.items():
                if addr:
                    result["chain"] = chain
                    result["contract"] = addr
                    break

            # CoinGecko没价格时（新币常见），从币安合约/现货补充
            if not result["price"]:
                binance_price = await _fetch_binance_price(symbol, client)
                if binance_price:
                    result["price"] = binance_price
                    ts = result.get("total_supply") or 0
                    cs = result.get("circ_supply") or 0
                    if ts > 0:
                        result["fdv"] = binance_price * ts
                    if cs > 0:
                        result["mcap"] = binance_price * cs
                    logger.info(f"币安补充价格 {symbol}: ${binance_price}, FDV=${result.get('fdv',0):,.0f}")

    except Exception as e:
        logger.warning(f"CoinGecko查询失败 {symbol}: {e}")
    return result


async def _fetch_binance_price(symbol: str, client: httpx.AsyncClient) -> float:
    """从币安现货或合约获取价格"""
    pair = f"{symbol.upper()}USDT"
    # 1. 现货
    try:
        resp = await client.get(f"https://api.binance.com/api/v3/ticker/price",
                                params={"symbol": pair})
        if resp.status_code == 200:
            return float(resp.json()["price"])
    except Exception:
        pass
    # 2. 合约
    try:
        resp = await client.get(f"https://fapi.binance.com/fapi/v1/ticker/price",
                                params={"symbol": pair})
        if resp.status_code == 200:
            return float(resp.json()["price"])
    except Exception:
        pass
    return 0.0


# ============================================================
# LLM 叙事抽取（可选，降级为规则）
# ============================================================

async def llm_extract(raw_text: str, symbol: str, name: str = "", cg_data: dict = None) -> dict:
    """用LLM从公告+CoinGecko数据抽取叙事/VC/是否亲儿子"""
    fallback = {
        "narrative": "unknown", "narrative_desc": "",
        "vcs": [], "is_darling": False, "exclude_reason": None,
    }

    # 从CoinGecko categories自动提取信息
    cg_data = cg_data or {}
    categories = cg_data.get("categories", [])
    description = cg_data.get("description", "")

    # 自动检测亲儿子（从categories）
    darling_cats = [c for c in categories if any(kw in c.lower() for kw in ["yzi labs", "binance labs"])]
    if darling_cats:
        fallback["is_darling"] = True

    if not ANTHROPIC_API_KEY:
        # 降级：从标题+categories关键词猜
        t = raw_text.lower()
        for kw in BINANCE_DARLING_KEYWORDS:
            if kw in t:
                fallback["is_darling"] = True
        # 从categories猜叙事
        cat_str = " ".join(categories).lower()
        if "defi" in cat_str: fallback["narrative"] = "defi"
        elif "ai" in cat_str: fallback["narrative"] = "ai_agent"
        elif "gaming" in cat_str or "gamefi" in cat_str: fallback["narrative"] = "gamefi"
        elif "meme" in cat_str: fallback["narrative"] = "meme"
        elif "rwa" in cat_str or "real world" in cat_str: fallback["narrative"] = "rwa"
        return fallback

    # 构建丰富的上下文
    extra_context = ""
    if categories:
        extra_context += f"\nCoinGecko分类: {', '.join(categories)}"
    if description:
        extra_context += f"\n项目描述: {description[:300]}"
    if cg_data.get("found"):
        extra_context += f"\n市场数据: FDV=${cg_data.get('fdv',0):,.0f}, MCap=${cg_data.get('mcap',0):,.0f}, 价格=${cg_data.get('price',0)}"
        if cg_data.get("chain"):
            extra_context += f", 链={cg_data['chain']}"

    system = "你是加密货币研究员，从币安公告和项目数据中提取关键信息。只返回JSON，无其他文字。"
    user = f"""分析这个币安上新项目：
代币: {symbol}, 项目名: {name or "未知"}
公告原文: {raw_text}
{extra_context}

返回JSON:
{{
  "narrative": "defi_perp|ai_agent|ai_defi|defai|zk_proof|infra|defi|rwa|gamefi|meme|social|stablecoin|unknown",
  "narrative_desc": "一句话中文描述这个项目做什么、有什么特点",
  "vcs": ["从CoinGecko分类和公告中提取的投资机构列表"],
  "is_darling": true/false,
  "exclude_reason": null|"already_tge"|"meme_only"
}}

判断规则:
- narrative: 选最主要的一个类别
- vcs: CoinGecko分类里如果有 "XXX Portfolio" 就提取XXX作为机构
- is_darling: 如果有YZi Labs/Binance Labs投资 或 CZ/何一站台 则true
- exclude_reason: 只有当项目在其他主要CEX(如Coinbase/OKX/Bybit)上线超过3个月才算"already_tge"。如果只是在DEX或刚在币安上线，不算already_tge。CoinGecko有价格数据不代表already_tge。纯meme无叙事则"meme_only"
"""

    try:
        async with httpx.AsyncClient(timeout=30, headers=HEADERS) as client:
            resp = await client.post(
                f"{ANTHROPIC_BASE_URL.rstrip('/')}/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": ANTHROPIC_MODEL,
                    "max_tokens": 800,
                    "temperature": 0,
                    "system": system,
                    "messages": [{"role": "user", "content": user}],
                }
            )
            if resp.status_code != 200:
                logger.warning(f"LLM调用失败 {resp.status_code}")
                return fallback
            data = resp.json()
            text = ""
            for block in data.get("content", []):
                if block.get("type") == "text":
                    text = block.get("text", "")
                    break
            text = text.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:-1])
            return json.loads(text)
    except Exception as e:
        logger.warning(f"LLM抽取异常: {e}")
        return fallback


# ============================================================
# 消息格式化
# ============================================================

def _fmt_mcap(v):
    if not v:
        return "N/A"
    if v >= 1e9:
        return f"${v/1e9:.1f}B"
    if v >= 1e6:
        return f"${v/1e6:.1f}M"
    if v >= 1e3:
        return f"${v/1e3:.0f}K"
    return f"${v:.0f}"


def _fmt_price(v):
    if not v:
        return "N/A"
    if v >= 1:
        return f"${v:.2f}"
    if v >= 0.01:
        return f"${v:.4f}"
    return f"${v:.6f}"


def fmt_discovery(p: dict) -> str:
    tier = p.get("tier", "C")
    icon = TIER_ICONS.get(tier, "⚪")
    label = TIER_LABELS.get(tier, "")
    symbol = p["symbol"]
    name = p.get("name") or ""
    vcs = json.loads(p.get("vcs_json", "[]")) if isinstance(p.get("vcs_json"), str) else p.get("vcs", [])

    lines = [
        f"{icon} <b>Alpha 首发 · ${symbol}</b> {icon}",
        f"📋 {label}",
        "",
        f"<b>{name}</b>" if name else "",
    ]

    if p.get("narrative_desc"):
        lines.append(f"💡 {p['narrative_desc']}")
    if p.get("narrative") and p["narrative"] != "unknown":
        lines.append(f"🏷 叙事: {p['narrative']}")
    lines.append("")

    if p.get("fdv"):
        lines.append(f"📊 FDV: {_fmt_mcap(p['fdv'])}")
    if p.get("circulating_mcap"):
        lines.append(f"📊 流通市值: {_fmt_mcap(p['circulating_mcap'])}")
    if p.get("open_price"):
        lines.append(f"💰 预估开盘价: {_fmt_price(p['open_price'])}")
    if p.get("total_supply") and p.get("circulating_supply"):
        pct = p["circulating_supply"] / p["total_supply"] * 100
        lines.append(f"📦 初始流通: {pct:.1f}%")

    if vcs:
        lines.append("")
        lines.append("🏛 <b>机构</b>")
        for v in vcs[:5]:
            is_t1 = any(t in v.lower() for t in TIER1_VCS)
            lines.append(f"  {'⭐' if is_t1 else '·'} {v}")

    if p.get("is_darling"):
        lines.append("")
        lines.append("🔥 <b>币安亲儿子</b>")

    if p.get("tier_reason"):
        lines.append("")
        lines.append(f"🎯 {p['tier_reason']}")

    lines.append("")
    lines.append(f"<i>📌 来源: {p.get('source', 'binance')}</i>")
    if p.get("raw_text"):
        lines.append(f"<i>{p['raw_text'][:120]}</i>")

    return "\n".join(l for l in lines if l is not None)


def fmt_countdown(p: dict, minutes: int) -> str:
    icon = TIER_ICONS.get(p.get("tier", "C"), "⚪")
    t = f"{minutes//60}h{minutes%60}m" if minutes >= 60 else f"{minutes}m"
    lines = [
        f"{icon} <b>倒计时提醒</b>",
        f"<b>${p['symbol']}</b> · {p.get('name', '')}",
        f"⏰ 距上线还有 <b>{t}</b>",
    ]
    if p.get("fdv"):
        lines.append(f"FDV: {_fmt_mcap(p['fdv'])}")
    if minutes <= 30:
        lines.append("🔔 <b>准备下单</b>")
    return "\n".join(lines)


def fmt_launch(p: dict, price: float, mcap: float, fdv: float) -> str:
    lines = [
        f"🚀 <b>${p['symbol']} 已上线</b>",
        f"开盘价: <b>{_fmt_price(price)}</b>",
        f"流通市值: <b>{_fmt_mcap(mcap)}</b>",
        f"FDV: <b>{_fmt_mcap(fdv)}</b>",
    ]
    return "\n".join(lines)


def fmt_periodic(p: dict, idx: int, price: float, mcap: float, change_pct: float) -> str:
    arrow = "📈" if change_pct > 0 else "📉"
    minutes = 30 * idx
    lines = [
        f"⏱ <b>${p['symbol']} · +{minutes}min</b>",
        f"流通市值: {_fmt_mcap(mcap)} ({arrow} {change_pct:+.1f}%)",
        f"当前价: {_fmt_price(price)}",
    ]
    if change_pct >= 100:
        lines.append("💡 <b>已翻倍，考虑分批止盈</b>")
    elif change_pct <= -30:
        lines.append("⚠️ 跌幅较大，评估是否止损")
    return "\n".join(lines)


def fmt_anomaly(p: dict, atype: str, price: float, change_pct: float) -> str:
    emoji = {"double": "🚀", "halve": "🔻"}.get(atype, "⚡")
    desc = {"double": "市值翻倍", "halve": "市值腰斩"}.get(atype, "异动")
    return f"{emoji} <b>${p['symbol']} {desc}</b>\n变化: {change_pct:+.1f}%\n当前价: {_fmt_price(price)}"


# ============================================================
# 核心逻辑: 公告监听
# ============================================================

async def announcement_listener():
    """轮询币安公告，发现新Alpha项目"""
    logger.info(f"📡 公告监听启动 · 轮询 {ANNOUNCEMENT_POLL_INTERVAL}s")
    while True:
        try:
            articles = await fetch_announcements()
            new_count = 0
            for art in articles:
                title = art.get("title", "")
                triggered, reason = is_trigger(title)
                if not triggered:
                    continue

                symbol = extract_symbol(title)
                if not symbol:
                    continue

                # 用发布日期去重
                release_ts = art.get("releaseDate")
                release_iso = datetime.fromtimestamp(release_ts / 1000).isoformat() if release_ts else ""
                launch_date = release_iso[:10] if release_iso else datetime.utcnow().date().isoformat()

                pid = project_id(symbol, launch_date)
                if project_exists(pid):
                    continue

                project = {
                    "id": pid,
                    "symbol": symbol,
                    "name": extract_name(title),
                    "launch_time": release_iso,
                    "source": "binance_announcement",
                    "raw_text": title,
                    "tier": "PENDING",
                    "vcs": [],
                    "is_darling": False,
                    "excluded": 0,
                }
                save_project(project)
                new_count += 1
                logger.info(f"🆕 发现 ${symbol}: {title[:80]}")

            if new_count:
                logger.info(f"本轮发现 {new_count} 个新项目")
        except Exception as e:
            logger.error(f"公告监听异常: {e}", exc_info=True)

        await asyncio.sleep(ANNOUNCEMENT_POLL_INTERVAL)


# ============================================================
# 核心逻辑: 聚合 + 推送
# ============================================================

async def aggregation_worker():
    """对PENDING项目做数据聚合、评级、推送"""
    logger.info(f"🧠 聚合工作者启动 · 轮询 {AGGREGATION_POLL_INTERVAL}s")
    while True:
        try:
            pending = list_pending()
            for p in pending:
                symbol = p["symbol"]
                try:
                    logger.info(f"📦 聚合 ${symbol}")

                    # 1. CoinGecko
                    cg = await fetch_coingecko(symbol, project_name=p.get('name', ''))
                    await asyncio.sleep(1)  # 避免限流

                    # 2. LLM抽取叙事（传入CoinGecko数据增强分析）
                    llm = await llm_extract(p.get("raw_text", ""), symbol, p.get("name"), cg_data=cg)
                    await asyncio.sleep(1)

                    # 3. 判断是否排除
                    if llm.get("exclude_reason") in ("already_tge", "meme_only"):
                        update_project(p["id"], {
                            "excluded": 1,
                            "exclude_reason": llm["exclude_reason"],
                            "tier": "EXCLUDED",
                        })
                        logger.info(f"⏭ ${symbol} 排除: {llm['exclude_reason']}")
                        continue

                    # 4. 数据校验 — CoinGecko数据可能匹配错或项目太新没数据
                    cg_fdv = cg.get("fdv", 0) or 0
                    cg_mcap = cg.get("mcap", 0) or 0
                    data_suspect = False
                    data_warnings = []

                    # 上币安的项目FDV不可能低于$100K（除非CoinGecko匹配错了）
                    if cg.get("found") and cg_fdv > 0 and cg_fdv < 100_000:
                        data_suspect = True
                        data_warnings.append(f"FDV=${cg_fdv:,.0f}异常低，可能CoinGecko匹配错误")
                        logger.warning(f"⚠️ {symbol} FDV=${cg_fdv:,.0f} 异常低，数据可能不准")

                    # 流通100%的大项目也要警惕（新代币通常不会100%流通）
                    if (cg.get("circ_supply") and cg.get("total_supply")
                            and cg["circ_supply"] == cg["total_supply"]
                            and cg_fdv < 1_000_000):
                        data_suspect = True
                        data_warnings.append("流通=总量且FDV极低，可能是同名垃圾币")

                    if data_suspect:
                        # 标记数据不可靠，评级时不使用CoinGecko的FDV/MCap
                        cg_fdv = 0
                        cg_mcap = 0
                        logger.warning(f"⚠️ {symbol} CoinGecko数据不可靠，评级使用LLM判断为主")

                    # 5. 评级
                    is_darling = llm.get("is_darling", False)
                    vcs = llm.get("vcs", [])
                    narrative = llm.get("narrative", "unknown")
                    rating = rate_project(
                        cg_mcap, cg_fdv,
                        vcs, narrative, is_darling
                    )

                    # 6. 更新DB（data_suspect时不存CoinGecko的错误数据）
                    update_project(p["id"], {
                        "tier": rating["tier"],
                        "tier_reason": rating["reason"],
                        "narrative": narrative,
                        "narrative_desc": llm.get("narrative_desc", ""),
                        "vcs_json": json.dumps(vcs),
                        "is_darling": int(is_darling),
                        "open_price": cg.get("price") if not data_suspect else None,
                        "total_supply": cg.get("total_supply") if not data_suspect else None,
                        "circulating_supply": cg.get("circ_supply") if not data_suspect else None,
                        "fdv": cg_fdv if cg_fdv else None,
                        "circulating_mcap": cg_mcap if cg_mcap else None,
                    })

                    # 6. 推送discovery
                    full = get_project(p["id"])
                    if full and not has_pushed(p["id"], "discovery"):
                        text = fmt_discovery(full)
                        silent = rating["tier"] in ("B", "C")
                        ok = await send_tg(text, silent=silent)
                        if ok:
                            log_push(p["id"], "discovery", text)
                            logger.info(f"✅ 推送 ${symbol} [{rating['tier']}]")

                except Exception as e:
                    logger.error(f"聚合 {symbol} 失败: {e}", exc_info=True)
                    update_project(p["id"], {"tier": "ERROR", "tier_reason": str(e)[:100]})

        except Exception as e:
            logger.error(f"聚合循环异常: {e}", exc_info=True)

        await asyncio.sleep(AGGREGATION_POLL_INTERVAL)


# ============================================================
# 核心逻辑: 上线后监控
# ============================================================

async def post_launch_monitor():
    """倒计时提醒 + 上线瞬间 + 30min×4跟踪 + 异动"""
    logger.info(f"📊 上线监控启动 · 轮询 {MONITOR_POLL_INTERVAL}s")
    while True:
        try:
            projects = list_active()
            for p in projects:
                try:
                    await _monitor_project(p)
                except Exception as e:
                    logger.error(f"监控 {p['symbol']} 异常: {e}")
        except Exception as e:
            logger.error(f"监控循环异常: {e}", exc_info=True)

        await asyncio.sleep(MONITOR_POLL_INTERVAL)


async def _monitor_project(p: dict):
    pid = p["id"]
    symbol = p["symbol"]
    launch_str = p.get("launch_time", "")
    if not launch_str:
        return

    try:
        launch = datetime.fromisoformat(launch_str.replace("Z", "").split("+")[0])
    except:
        return

    now = datetime.utcnow()
    delta_sec = (launch - now).total_seconds()

    # T-3h
    if 3*3600 - 300 <= delta_sec <= 3*3600 + 300:
        if not has_pushed(pid, "t_minus_3h"):
            text = fmt_countdown(p, int(delta_sec / 60))
            ok = await send_tg(text, silent=p.get("tier") in ("B", "C"))
            if ok:
                log_push(pid, "t_minus_3h", text)

    # T-30m
    elif 30*60 - 150 <= delta_sec <= 30*60 + 150:
        if not has_pushed(pid, "t_minus_30m"):
            text = fmt_countdown(p, int(delta_sec / 60))
            ok = await send_tg(text, silent=False)
            if ok:
                log_push(pid, "t_minus_30m", text)

    # 上线瞬间
    elif -300 <= delta_sec <= 0:
        if not has_pushed(pid, "at_launch"):
            cg = await fetch_coingecko(symbol, project_name=p.get('name', ''))
            if cg.get("price"):
                text = fmt_launch(p, cg["price"], cg.get("mcap", 0), cg.get("fdv", 0))
                ok = await send_tg(text, silent=False)
                if ok:
                    log_push(pid, "at_launch", text)
                    save_snapshot(pid, cg["price"], cg.get("mcap", 0), cg.get("fdv", 0))

    # 上线后30min × 4
    elif 0 < -delta_sec <= 2.5 * 3600:
        minutes_after = int(-delta_sec / 60)
        for idx, target in enumerate([30, 60, 90, 120], 1):
            if abs(minutes_after - target) <= 5:
                ptype = f"post_30m_{idx}"
                if not has_pushed(pid, ptype):
                    cg = await fetch_coingecko(symbol, project_name=p.get('name', ''))
                    if cg.get("price"):
                        open_price = p.get("open_price") or cg["price"]
                        change = ((cg["price"] - open_price) / open_price * 100) if open_price else 0
                        text = fmt_periodic(p, idx, cg["price"], cg.get("mcap", 0), change)
                        ok = await send_tg(text, silent=p.get("tier") in ("B", "C") and idx > 1)
                        if ok:
                            log_push(pid, ptype, text)
                            save_snapshot(pid, cg["price"], cg.get("mcap", 0), cg.get("fdv", 0))

                        # 异动
                        if change >= 100 and not has_pushed(pid, "anomaly_double"):
                            t = fmt_anomaly(p, "double", cg["price"], change)
                            if await send_tg(t):
                                log_push(pid, "anomaly_double", t)
                        elif change <= -50 and not has_pushed(pid, "anomaly_halve"):
                            t = fmt_anomaly(p, "halve", cg["price"], change)
                            if await send_tg(t):
                                log_push(pid, "anomaly_halve", t)
                    break


# ============================================================
# 启动
# ============================================================

async def main():
    init_db()
    logger.info(f"📂 数据库: {DB_PATH}")

    # 测试TG
    ok = await send_tg("🎉 <b>Alpha Monitor v2 启动</b>\n\n📡 币安公告监听中...\n🔔 有新Alpha会立即推送")
    if ok:
        logger.info("✅ TG推送正常")
    else:
        logger.warning("⚠️ TG推送失败，检查配置")

    tasks = [
        asyncio.create_task(announcement_listener(), name="announcements"),
        asyncio.create_task(aggregation_worker(), name="aggregator"),
        asyncio.create_task(post_launch_monitor(), name="monitor"),
    ]

    logger.info("=" * 50)
    logger.info("🚀 Alpha Monitor v2 启动完成")
    logger.info(f"  📡 公告轮询: {ANNOUNCEMENT_POLL_INTERVAL}s")
    logger.info(f"  🧠 LLM: {'Sonnet' if ANTHROPIC_API_KEY else '降级(规则)'}")
    logger.info(f"  🔔 TG: {'✅' if TG_BOT_TOKEN else '❌'}")
    logger.info("=" * 50)

    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        for t in tasks:
            t.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
