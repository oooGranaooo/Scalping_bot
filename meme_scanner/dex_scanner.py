from __future__ import annotations

import logging
import time

import requests

import config

logger = logging.getLogger(__name__)

_GT_TRENDING_URL = f"{config.GT_BASE_URL}/networks/solana/trending_pools"


def get_filtered_pairs() -> list[dict]:
    """
    GeckoTerminal のトレンドプール一覧（Solana）を取得し、以下の順でフィルタリングする：
      1. MCレンジ（MC_MIN〜MC_MAX）と流動性（LIQ_MIN以上）でフィルタ
      2. MCの降順でソート
      3. 上位10件に絞る

    Returns: MCの高い順に最大10件のペアリスト（正規化済み辞書）
    """
    all_pools: list[dict] = []

    # page=1 で20件取得（無料プランは1ページのみ）
    for page in (1, 2):
        try:
            resp = requests.get(
                _GT_TRENDING_URL,
                params={"page": page, "duration": config.GT_TRENDING_DURATION},
                headers=config.GT_HEADERS,
                timeout=10,
            )
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"GeckoTerminal trending_pools APIリクエスト失敗 (page={page}): {e}")
            break

        pools = resp.json().get("data", [])
        if not pools:
            break
        all_pools.extend(pools)
        time.sleep(config.GT_REQUEST_INTERVAL)

    filtered = []
    for pool in all_pools:
        attrs = pool.get("attributes", {})
        mc  = _to_float(attrs.get("market_cap_usd") or attrs.get("fdv_usd"))
        liq = _to_float(attrs.get("reserve_in_usd"))

        if mc == 0:
            continue
        if config.MC_MIN <= mc <= config.MC_MAX and liq >= config.LIQ_MIN:
            pool["_mc"] = mc
            pool["_liq"] = liq
            filtered.append(pool)

    filtered.sort(key=lambda p: p["_mc"], reverse=True)
    top = filtered[:10]

    return [_normalize(p) for p in top]


def _to_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _normalize(pool: dict) -> dict:
    attrs = pool.get("attributes", {})
    rels  = pool.get("relationships", {})

    mc  = pool["_mc"]
    liq = pool["_liq"]

    # pool address（プールアドレス）
    pair_address = attrs.get("address", "")

    # トークンアドレス: "solana_<address>" → "<address>"
    base_token_id = rels.get("base_token", {}).get("data", {}).get("id", "")
    token_address = base_token_id.replace("solana_", "", 1)

    # シンボル / 名前: "TOKEN / SOL" → symbol="TOKEN", name="TOKEN"
    pool_name = attrs.get("name", "UNKNOWN / SOL")
    symbol = pool_name.split(" / ")[0].strip()
    name   = symbol  # GeckoTerminal trending_pools では正式名称が取れないためシンボル流用

    volume_usd = attrs.get("volume_usd", {})
    price_chg  = attrs.get("price_change_percentage", {})

    gecko_url = f"https://www.geckoterminal.com/solana/pools/{pair_address}"

    return {
        "symbol":        symbol,
        "name":          name,
        "token_address": token_address,
        "pair_address":  pair_address,
        "mc":            mc,
        "liquidity":     liq,
        "volume_h1":     _to_float(volume_usd.get("h1")),
        "volume_h24":    _to_float(volume_usd.get("h24")),
        "price_change": {
            "m5":  _to_float(price_chg.get("m5")),
            "h1":  _to_float(price_chg.get("h1")),
            "h6":  _to_float(price_chg.get("h6")),
            "h24": _to_float(price_chg.get("h24")),
        },
        "gecko_url": gecko_url,
    }


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    pairs = get_filtered_pairs()
    print(f"\nフィルタ通過: {len(pairs)}件\n")
    for p in pairs:
        print(
            f"  {p['symbol']:10s}  MC=${p['mc']:>12,.0f}  "
            f"liq=${p['liquidity']:>10,.0f}  {p['token_address']}"
        )
