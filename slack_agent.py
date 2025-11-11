#!/usr/bin/env python3
import os
import csv
import datetime
import requests
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# ==== .env 読み込み（ローカル用、GitHub Actions上ではSecretsからenvに入る想定） ====
env_path = Path(__file__).with_name(".env")
if env_path.exists():
    load_dotenv(env_path)

# ==== 環境変数 ====
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SLACK_USER_TOKEN = os.getenv("SLACK_USER_TOKEN")

assert OPENAI_API_KEY, "OPENAI_API_KEY が設定されていません"
assert SLACK_USER_TOKEN, "SLACK_USER_TOKEN が設定されていません"

# Slack 投稿先
SLACK_CHANNEL = "all-動作検証用"  # 必要なら "#..." や チャンネルID に変更

# 対象（Stooqのシンボル）
INDEX_SYMBOLS = {
    "^NKX": "日経平均",  # Nikkei 225
    "^TPX": "TOPIX",
}

STOCK_SYMBOLS = {
    "7203.JP": "トヨタ自動車",
    "6758.JP": "ソニーグループ",
    "9984.JP": "ソフトバンクG",
    "7974.JP": "任天堂",
    "8035.JP": "東京エレクトロン",
    "8306.JP": "三菱UFJFG",
    "8316.JP": "三井住友FG",
    "6861.JP": "キーエンス",
}

openai_client = OpenAI(api_key=OPENAI_API_KEY)
slack_client = WebClient(token=SLACK_USER_TOKEN)


def fetch_from_stooq(symbol: str):
    """
    Stooq CSV (日足) から直近2営業日の終値を取得し、前日比・騰落率を返す。
    URL形式: https://stooq.com/q/d/l/?s=<symbol>&i=d
    """
    url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"[ERROR] {symbol}: HTTPエラー: {e}")
        return None

    # CSVをパース
    lines = resp.text.strip().splitlines()
    if len(lines) <= 1:
        print(f"[WARN] {symbol}: 行数不足: {len(lines)}")
        return None

    reader = csv.DictReader(lines)
    rows = [row for row in reader if row.get("Close") not in ("", "0", "0.00", None)]

    if len(rows) < 2:
        print(f"[WARN] {symbol}: 有効なデータ行が足りません")
        return None

    # 直近2営業日（CSVは過去→現在の順 or 逆の場合もあるので、日付でソート）
    rows.sort(key=lambda r: r["Date"])
    latest = rows[-1]
    prev = rows[-2]

    try:
        price = float(latest["Close"])
        prev_close = float(prev["Close"])
    except ValueError:
        print(f"[WARN] {symbol}: 数値変換エラー latest={latest['Close']}, prev={prev['Close']}")
        return None

    if prev_close == 0:
        print(f"[WARN] {symbol}: 前日終値が0")
        return None

    change = price - prev_close
    change_pct = change / prev_close * 100.0

    return {
        "price": price,
        "prev_close": prev_close,
        "change": change,
        "change_pct": change_pct,
    }


def build_index_section() -> str:
    lines = []
    for symbol, label in INDEX_SYMBOLS.items():
        q = fetch_from_stooq(symbol)
        if q:
            sign = "+" if q["change"] >= 0 else ""
            lines.append(
                f"- {label}: {q['price']:.2f} ({sign}{q['change']:.2f}, {sign}{q['change_pct']:.2f}%)"
            )
        else:
            lines.append(f"- {label}: データ取得失敗")
    return "\n".join(lines)


def build_stock_rankings() -> str:
    results = []
    for symbol, name in STOCK_SYMBOLS.items():
        q = fetch_from_stooq(symbol)
        if not q:
            continue
        results.append(
            {
                "symbol": symbol,
                "name": name,
                "price": q["price"],
                "change_pct": q["change_pct"],
            }
        )

    if not results:
        return "■ 個別銘柄: データ取得に失敗しました。"

    # 騰落率ソート
    sorted_by = sorted(results, key=lambda x: x["change_pct"], reverse=True)
    gainers = sorted_by[:3]
    losers = sorted(sorted_by[-3:], key=lambda x: x["change_pct"])

    def fmt(e):
        sign = "+" if e["change_pct"] >= 0 else ""
        return f"{e['name']} ({sign}{e['change_pct']:.2f}%)"

    lines = []
    lines.append("■ 値上がり率 上位（代表大型株）")
    for e in gainers:
        lines.append(f"- {fmt(e)}")

    lines.append("■ 値下がり率 上位（代表大型株）")
    for e in losers:
        lines.append(f"- {fmt(e)}")

    up = sum(1 for r in results if r["change_pct"] > 0)
    down = sum(1 for r in results if r["change_pct"] < 0)
    flat = len(results) - up - down
    lines.append(f"■ 地合い（代表大型株ベース）: 上昇 {up}, 下落 {down}, 横ばい {flat}")

    return "\n".join(lines)


def build_market_snapshot_text() -> str:
    jst = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    header = f"日本株マーケットスナップショット (JST {jst:%Y-%m-%d %H:%M:%S})"
    index_section = build_index_section()
    ranking_section = build_stock_rankings()
    return f"{header}\n\n■ 主要指数\n{index_section}\n\n{ranking_section}"


def build_summary(market_snapshot: str) -> str:
    prompt = f"""
以下は、日本株市場（主要指数＋代表的な大型株）のスナップショットです：

{market_snapshot}

この情報をもとに、日本株マーケットサマリーを日本語で作成してください。

要件:
- 先頭に「【日本株マーケットサマリー】」と入れる
- 箇条書き 3〜7行程度
- 日経平均 / TOPIX の方向感
- 値上がり / 値下がり上位の代表銘柄から読み取れるテーマ
- 地合い（全面高・全面安・まちまち 等）を一言で
- データ取得失敗があれば、それも正直に触れる
- 初心者にも分かりやすい日本語
- 最後に必ず次の一文を含める：
  「※このサマリーはStooq等のデータを元に自動生成された参考情報であり、正確性・完全性・将来の成果を保証するものではありません。」

出力はSlackに投稿可能なテキストのみ。
"""
    res = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": "あなたは日本株の動向を簡潔かつ中立的に要約するアナリストです。",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.4,
    )
    return res.choices[0].message.content.strip()


def post_to_slack(text: str):
    try:
        resp = slack_client.chat_postMessage(
            channel=SLACK_CHANNEL,
            text=text,
        )
        print("Slack への投稿に成功しました。ts:", resp["ts"])
    except SlackApiError as e:
        print("Slack 投稿エラー:", e.response.get("error"))
        raise

def main():
    snapshot = build_market_snapshot_text()
    print("=== Market snapshot (raw) ===")
    print(snapshot)

    summary = build_summary(snapshot)
    print("=== Generated summary ===")
    print(summary)

    # 指標データ＋サマリーをまとめて投稿
    message = snapshot + "\n\n" + summary

    post_to_slack(message)



if __name__ == "__main__":
    main()
