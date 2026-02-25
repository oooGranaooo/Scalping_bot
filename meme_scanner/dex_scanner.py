import logging
import time

import requests

import config

logger = logging.getLogger(__name__)


def get_filtered_pairs() -> list[dict]:
    """
    DexScreenerからSolanaペアを取得し、以下の順でフィルタリングする：
      1. MCレンジ（MC_MIN〜MC_MAX）と流動性（LIQ_MIN以上）でフィルタ
      2. MCの降順でソート
      3. 上位10件に絞る

    Returns: MCの高い順に最大10件のペアリスト（正規化済み辞書）
    """
    url = f"{config.DEX_BASE_URL}/latest/dex/pairs/solana"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"DexScreener APIリクエスト失敗: {e}")
        return []

    pairs = resp.json().get("pairs", [])

    filtered = []
    for pair in pairs:
        mc  = float(pair.get("marketCap") or pair.get("fdv") or 0)
        liq = float((pair.get("liquidity") or {}).get("usd") or 0)

        if config.MC_MIN <= mc <= config.MC_MAX and liq >= config.LIQ_MIN:
            pair["_mc"] = mc
            filtered.append(pair)

    filtered.sort(key=lambda p: p["_mc"], reverse=True)
    top = filtered[:10]

    return [_normalize(p) for p in top]


def _normalize(pair: dict) -> dict:
    mc  = pair["_mc"]
    liq = float((pair.get("liquidity") or {}).get("usd") or 0)
    return {
        "symbol":        pair["baseToken"]["symbol"],
        "name":          pair["baseToken"]["name"],
        "token_address": pair["baseToken"]["address"],
        "pair_address":  pair["pairAddress"],
        "mc":            mc,
        "liquidity":     liq,
        "volume_h1":     float((pair.get("volume") or {}).get("h1") or 0),
        "volume_h24":    float((pair.get("volume") or {}).get("h24") or 0),
        "price_change": {
            "m5":  float((pair.get("priceChange") or {}).get("m5") or 0),
            "h1":  float((pair.get("priceChange") or {}).get("h1") or 0),
            "h6":  float((pair.get("priceChange") or {}).get("h6") or 0),
            "h24": float((pair.get("priceChange") or {}).get("h24") or 0),
        },
        "dex_url": pair.get("url", ""),
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
