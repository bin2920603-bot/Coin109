#!/usr/bin/env python3
"""업비트 화면의 거래대금(24시간 누적)을 15분마다 기록하고, 시총 순위를 함께 모은다."""
import json, os, time
from datetime import datetime, timedelta, timezone
import requests

# ── 관심 109종목 (쉼표로 구분. 추가/삭제는 여기만 고치면 됨) ──────────────
CODES = (
    "FIL,XTZ,BAT,CELO,API3,BSV,BLUR,A,ZETA,THETA,NEO,AGLD,GMT,ORDER,ASTR,KNC,"
    "CHZ,GRT,CAP,ME,MANA,ANIME,BABY,PYTH,QTUM,W,BIGTIME,AUCTION,GAS,IOTA,TIA,"
    "SENT,0G,YGG,ATH,ALGO,CFX,STX,ICP,HBAR,VANA,BREV,LA,MASK,TAO,MEW,WAL,LAYER,"
    "F,APT,ZRX,ZRO,SPX,DOS,AAVE,ZORA,1INCH,OPG,BTT,BARD,ARX,ZAMA,DOT,GRVT,ATOM,"
    "WIF,ADA,AVAX,ONT,TRUST,BTC,ZK,PEPE,DOGE,IO,PLUME,ETC,TRUMP,AERO,BERA,SPK,"
    "SOL,LINK,NEAR,SHIB,ETH,ENA,ENS,WLD,LINEA,KMNO,ENSO,COMP,PIEVERSE,EGLD,"
    "SYRUP,PENDLE,PROS,DRV,JUP,CRO,RAY,SIGN,DOOD,MINA,ESP,CHIP,ETHFI"
)
# 티커를 몰라서 한글명으로 찾을 종목
NAMES = ["디피니티브"]

# 시총 순위가 엉뚱하게 잡히면 여기에 "KRW-A": "코인게코id" 형태로 고정
OVERRIDE = {}

KST = timezone(timedelta(hours=9))
ROOT = os.path.dirname(os.path.abspath(__file__))
VOL = os.path.join(ROOT, "data", "volumes.json")
CAP = os.path.join(ROOT, "data", "marketcap.json")
KEEP_DAYS = 7


def get(url, params=None, tries=4):
    for i in range(tries):
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 429:
            time.sleep(1.5 * (i + 1))
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()


def resolve():
    markets = get("https://api.upbit.com/v1/market/all", {"isDetails": "false"})
    by_code = {m["market"]: m["korean_name"] for m in markets if m["market"].startswith("KRW-")}
    by_name = {m["korean_name"]: m["market"] for m in markets if m["market"].startswith("KRW-")}

    out, missing = [], []
    for c in CODES.split(","):
        code = "KRW-" + c.strip()
        if code in by_code:
            out.append({"market": code, "name": by_code[code]})
        else:
            missing.append(code)
    for n in NAMES:
        if n in by_name:
            print(f"[찾음] {n} -> {by_name[n]}")
            out.append({"market": by_name[n], "name": n})
        else:
            missing.append(n)
    if missing:
        print("[확인필요] 업비트 원화마켓에 없음: " + ", ".join(missing))
    return out


def slot_now():
    """지금 시각을 15분 단위로 내림 (예: 02:37 -> 02:30)"""
    now = datetime.now(KST)
    return now.replace(minute=now.minute // 15 * 15, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M")


def collect_volumes(coins):
    store = json.load(open(VOL, encoding="utf-8")) if os.path.exists(VOL) else {"coins": {}}
    slot = slot_now()

    # 시세 조회는 한 번에 여러 종목을 받을 수 있어 100개씩 끊어서 요청
    tickers, failed = {}, []
    codes = [c["market"] for c in coins]
    for i in range(0, len(codes), 100):
        chunk = codes[i:i + 100]
        try:
            for t in get("https://api.upbit.com/v1/ticker", {"markets": ",".join(chunk)}):
                tickers[t["market"]] = t
        except Exception as e:
            failed += chunk
            print(f"  ! 조회 실패: {e}")
        time.sleep(0.2)

    for meta in coins:
        code = meta["market"]
        t = tickers.get(code)
        if not t:
            if code not in failed:
                failed.append(code)
            continue
        entry = store["coins"].setdefault(code, {"name": meta["name"], "slots": {}})
        entry["name"] = meta["name"]
        entry["price"] = t["trade_price"]
        entry["change"] = round(t.get("signed_change_rate", 0) * 100, 2)
        # 업비트 화면의 '거래대금' = 24시간 누적 거래대금
        entry["slots"][slot] = round(t["acc_trade_price_24h"])

    cut = (datetime.now(KST) - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%dT%H:%M")
    for e in store["coins"].values():
        e["slots"] = {k: v for k, v in e["slots"].items() if k >= cut}

    store["updated_at"] = datetime.now(KST).isoformat(timespec="seconds")
    store["failed"] = failed
    os.makedirs(os.path.dirname(VOL), exist_ok=True)
    json.dump(store, open(VOL, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"{slot} 저장 · 성공 {len(coins) - len(failed)} / 실패 {len(failed)}")


def collect_marketcap(coins):
    if os.path.exists(CAP):
        try:
            prev = json.load(open(CAP, encoding="utf-8"))
            if datetime.now(KST) - datetime.fromisoformat(prev["updated_at"]) < timedelta(hours=12):
                print("시총 순위는 12시간 내 갱신됨 · 건너뜀")
                return
        except Exception:
            pass

    by_symbol, by_id = {}, {}
    for page in range(1, 5):
        rows = get("https://api.coingecko.com/api/v3/coins/markets",
                   {"vs_currency": "usd", "order": "market_cap_desc", "per_page": 250, "page": page})
        for c in rows:
            rank = c.get("market_cap_rank")
            if not rank:
                continue
            by_id[c["id"]] = rank
            s = (c["symbol"] or "").upper()
            if s not in by_symbol or rank < by_symbol[s]:
                by_symbol[s] = rank
        time.sleep(2)

    ranks, unknown = {}, []
    for meta in coins:
        code = meta["market"]
        rank = by_id.get(OVERRIDE[code]) if code in OVERRIDE else by_symbol.get(code.split("-", 1)[1])
        if rank:
            ranks[code] = rank
        else:
            unknown.append(meta["name"])
    if unknown:
        print("[시총 못찾음] " + ", ".join(unknown))

    os.makedirs(os.path.dirname(CAP), exist_ok=True)
    json.dump({"updated_at": datetime.now(KST).isoformat(timespec="seconds"), "ranks": ranks},
              open(CAP, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"시총 순위 {len(ranks)}개 저장")


if __name__ == "__main__":
    coins = resolve()
    print(f"수집 대상 {len(coins)}개")
    collect_volumes(coins)
    try:
        collect_marketcap(coins)
    except Exception as e:
        print(f"시총 수집 건너뜀: {e}")
