#!/usr/bin/env python3
import os
import csv
import time
import datetime
import requests
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError


# ==============================
# 環境変数・設定読み込み
# ==============================
env_path = Path(__file__).with_name(".env")
if env_path.exists():
    load_dotenv(env_path)

OPENAI_API_KEY   = os.getenv("OPENAI_API_KEY")
SLACK_BOT_TOKEN  = os.getenv("SLACK_BOT_TOKEN")   # ← xoxb- ボットトークンを使う
PAGES_URL        = os.getenv("PAGES_URL", "https://na-hiro.github.io/github_actions_test/list.html")

# 投稿先は ID 優先。無ければ名前で検索解決を試みる
SLACK_CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID")  # 例: C0123456789 （推奨）
SLACK_CHANNEL    = os.getenv("SLACK_CHANNEL", "all-動作検証用")

assert OPENAI_API_KEY,  "OPENAI_API_KEY が設定されていません"
assert SLACK_BOT_TOKEN, "SLACK_BOT_TOKEN が設定されていません（xoxb-）"

openai_client = OpenAI(api_key=OPENAI_API_KEY)
slack_client  = WebClient(token=SLACK_BOT_TOKEN)

info = slack_client.api_call("auth.test")
print("AUTH TEST:", info)

# ==============================
# ティッカー設定読み込み
# ==============================
def load_symbols():
    """
    tickers.csv: type,symbol,label
      type: index / stock / gold
    """
    cfg = Path(__file__).with_name("tickers.csv")
    index_symbols, stock_symbols, gold_symbols = {}, {}, {}

    if not cfg.exists():
        index_symbols = {"^NKX": "日経平均", "^TPX": "TOPIX"}
        stock_symbols = {
            "7203.JP": "トヨタ自動車",
            "6758.JP": "ソニーグループ",
            "9984.JP": "ソフトバンクG",
            "7974.JP": "任天堂",
            "8035.JP": "東京エレクトロン",
            "8306.JP": "三菱UFJFG",
            "8316.JP": "三井住友FG",
            "6861.JP": "キーエンス",
        }
        return index_symbols, stock_symbols, gold_symbols

    with cfg.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            t    = (row.get("type")   or "").strip().lower()
            sym  = (row.get("symbol") or "").strip()
            name = (row.get("label")  or "").strip()
            if not t or not sym or not name:
                continue
            if   t == "index": index_symbols[sym] = name
            elif t == "stock": stock_symbols[sym] = name
            elif t == "gold":  gold_symbols[sym]  = name

    if not index_symbols:
        index_symbols = {"^NKX": "日経平均", "^TPX": "TOPIX"}

    return index_symbols, stock_symbols, gold_symbols

INDEX_SYMBOLS, STOCK_SYMBOLS, GOLD_SYMBOLS = load_symbols()

# ==============================
# データ取得（Stooq）
# ==============================
def fetch_from_stooq(symbol: str, retries: int = 2, timeout: int = 10):
    """Stooq日足CSVから直近2営業日を使って前日比を算出"""
    url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, timeout=timeout)
            resp.raise_for_status()
            lines = resp.text.strip().splitlines()
            if len(lines) <= 1:
                print(f"[WARN] {symbol}: 行数不足")
                return None
            reader = csv.DictReader(lines)
            rows = [r for r in reader if r.get("Close") and r.get("Date")]
            if len(rows) < 2:
                print(f"[WARN] {symbol}: 有効データ不足")
                return None
            rows.sort(key=lambda r: r["Date"])
            latest, prev = rows[-1], rows[-2]
            price = float(latest["Close"])
            prevc = float(prev["Close"])
            if prevc == 0:
                print(f"[WARN] {symbol}: prev close 0")
                return None
            change = price - prevc
            pct = change / prevc * 100.0
            return {"price": price, "prev_close": prevc, "change": change, "change_pct": pct}
        except Exception as e:
            if attempt < retries:
                time.sleep(1.0)
                continue
            print(f"[ERROR] {symbol} fetch failed: {e}")
            return None

# ==============================
# 各セクション生成
# ==============================
def build_index_section() -> str:
    lines = []
    for symbol, label in INDEX_SYMBOLS.items():
        q = fetch_from_stooq(symbol)
        if q:
            sign = "+" if q["change"] >= 0 else ""
            lines.append(f"- {label}: {q['price']:.2f} ({sign}{q['change']:.2f}, {sign}{q['change_pct']:.2f}%)")
        else:
            lines.append(f"- {label}: データ取得失敗")
    return "\n".join(lines)

def build_stock_rankings() -> str:
    results = []
    for symbol, name in STOCK_SYMBOLS.items():
        q = fetch_from_stooq(symbol)
        if q:
            results.append({"name": name, "change_pct": q["change_pct"]})
    if not results:
        return "■ 指定銘柄: データ取得に失敗しました。"
    results.sort(key=lambda x: x["change_pct"], reverse=True)
    gainers = results[:3]
    losers  = sorted(results[-3:], key=lambda x: x["change_pct"])
    def fmt(e): return f"{e['name']} ({'+' if e['change_pct']>=0 else ''}{e['change_pct']:.2f}%)"
    up = sum(1 for r in results if r["change_pct"] > 0)
    dn = sum(1 for r in results if r["change_pct"] < 0)
    fl = len(results) - up - dn
    return "\n".join([
        "■ 値上がり率 上位（指定銘柄）",
        *[f"- {fmt(x)}" for x in gainers],
        "■ 値下がり率 上位（指定銘柄）",
        *[f"- {fmt(x)}" for x in losers],
        f"■ 地合い（指定銘柄ベース）: 上昇 {up}, 下落 {dn}, 横ばい {fl}",
    ])

def build_gold_section() -> str:
    if not GOLD_SYMBOLS:
        return ""
    lines = ["■ 金価格など"]
    for symbol, label in GOLD_SYMBOLS.items():
        q = fetch_from_stooq(symbol)
        if q:
            sign = "+" if q["change"] >= 0 else ""
            lines.append(f"- {label}: {q['price']:.2f} ({sign}{q['change']:.2f}, {sign}{q['change_pct']:.2f}%)")
        else:
            lines.append(f"- {label}: データ取得失敗")
    return "\n".join(lines)

def build_market_snapshot_text() -> str:
    jst = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    header = f"日本株マーケットスナップショット (JST {jst:%Y-%m-%d %H:%M:%S})"
    parts = [header, "■ 主要指数", build_index_section(), build_stock_rankings()]
    gold = build_gold_section()
    if gold:
        parts.append(gold)
    return "\n\n".join(parts)

# ==============================
# GPT サマリー
# ==============================
def build_summary(market_snapshot: str) -> str:
    prompt = f"""
以下は、日本株市場（主要指数＋指定銘柄＋金価格など）のスナップショットです：

{market_snapshot}

この情報をもとに、日本株マーケットサマリーを日本語で作成してください。

要件:
- 先頭に「【日本株マーケットサマリー】」
- 箇条書き 3〜7行
- 指数（日経平均・TOPIX）の方向感
- 指定銘柄の値上がり/値下がりから読み取れるテーマ
- 金価格などがあれば一言
- 地合い（全面高・全面安・まちまち）
- 失敗箇所があれば正直に明記
- 最後に必ず次の一文：
  「※このサマリーはStooq等のデータを元に自動生成された参考情報であり、正確性・完全性・将来の成果を保証するものではありません。」
出力はSlackに投稿可能なテキストのみ。
"""
    res = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "あなたは日本株の動向を簡潔かつ中立的に要約するアナリストです。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.4,
    )
    return res.choices[0].message.content.strip()

# ==============================
# Slack 投稿（ID解決含む）
# ==============================
def resolve_channel_id_by_name(name: str) -> str | None:
    """チャンネル名(#なし)からIDを引く（要: channels:read）。失敗時はNone"""
    try:
        cursor = None
        while True:
            resp = slack_client.conversations_list(limit=1000, cursor=cursor)
            for ch in resp.get("channels", []):
                if ch.get("name") == name.lstrip("#"):
                    return ch.get("id")
            cursor = resp.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
    except SlackApiError as e:
        print("[WARN] conversations.list 失敗:", e.response.get("error"))
    return None

def post_to_slack(text: str):
    channel_id = SLACK_CHANNEL_ID
    if not channel_id:
        # 名前からID解決を試す
        channel_id = resolve_channel_id_by_name(SLACK_CHANNEL) or SLACK_CHANNEL
    try:
        resp = slack_client.chat_postMessage(channel=channel_id, text=text)
        print("Slack への投稿に成功しました。ts:", resp["ts"])
    except SlackApiError as e:
        print("Slack 投稿エラー:", e.response.get("error"))
        raise

# ==============================
# メイン
# ==============================
def main():
    snapshot = build_market_snapshot_text()
    print("=== Market snapshot (raw) ===")
    print(snapshot)

    summary = build_summary(snapshot)
    print("=== Generated summary ===")
    print(summary)

    message = (
        f"【インタラクティブチャート（GitHub Pages）】\n{PAGES_URL}\n\n"
        f"{snapshot}\n\n{summary}"
    )
    post_to_slack(message)

if __name__ == "__main__":
    main()
