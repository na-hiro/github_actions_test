#!/usr/bin/env python3
import os, csv, requests, datetime
from typing import List
from openai import OpenAI

# Slack Bolt（共通）
from slack_bolt import App as SlackApp
from slack_sdk import WebClient

# HTTP用（RUN_MODE=http のとき使用）
from fastapi import FastAPI, Request
from slack_bolt.adapter.fastapi import SlackRequestHandler

from pathlib import Path
from dotenv import load_dotenv

env_path = Path(__file__).with_name(".env")
if env_path.exists():
    load_dotenv(env_path)

# ===== 環境変数 =====
RUN_MODE             = os.getenv("RUN_MODE", "socket").lower()   # "socket" or "http"
OPENAI_API_KEY       = os.environ["OPENAI_API_KEY"]
SLACK_BOT_TOKEN      = os.environ["SLACK_BOT_TOKEN"]             # xoxb-...
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET")         # http時に必須
SLACK_APP_TOKEN      = os.getenv("SLACK_APP_TOKEN")              # xapp-...（socket時に必須）
PAGES_BASE           = os.getenv("PAGES_BASE", "https://na-hiro.github.io/github_actions_test/").rstrip("/")

# ===== クライアント =====
client    = OpenAI(api_key=OPENAI_API_KEY)
slack_app = SlackApp(token=SLACK_BOT_TOKEN, signing_secret=SLACK_SIGNING_SECRET)
slack_web = WebClient(token=SLACK_BOT_TOKEN)

# ===== 参照シンボル（必要なら tickers.csv 連携に差し替え可）=====
INDEX_SYMBOLS = {"^NKX": "日経平均", "^TPX": "TOPIX"}
STOCK_SYMBOLS = {"7203.JP": "トヨタ自動車", "6758.JP": "ソニーG", "9984.JP": "ソフトバンクG"}
GOLD_SYMBOLS  = {"XAUUSD": "金(USD)", "XAUJPY": "金(JPY)"}

def fetch_from_stooq(symbol: str):
    url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
    r = requests.get(url, timeout=10); r.raise_for_status()
    rows = list(csv.DictReader(r.text.strip().splitlines()))
    rows = [x for x in rows if x.get("Close")]
    if len(rows) < 2: return None
    rows.sort(key=lambda x: x["Date"])
    last, prev = rows[-1], rows[-2]
    price, prev_c = float(last["Close"]), float(prev["Close"])
    chg = price - prev_c; pct = (chg/prev_c*100.0) if prev_c else 0.0
    return {"price": price, "change": chg, "change_pct": pct, "date": last["Date"]}

def chart_url(symbol: str) -> str:
    safe = symbol.replace("^","").replace(".","_")
    return f"{PAGES_BASE}/{safe}.html"

def pages_index() -> str:
    return f"{PAGES_BASE}/index.html"

def pages_list() -> str:
    return f"{PAGES_BASE}/list.html"

def build_market_snapshot_text() -> str:
    jst = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    head = f"日本株マーケットスナップショット (JST {jst:%Y-%m-%d %H:%M:%S})"

    def fmt_lines(sym_map):
        lines = []
        for sym, label in sym_map.items():
            q = fetch_from_stooq(sym)
            if q:
                s = "+" if q["change"] >= 0 else ""
                lines.append(f"- {label}: {q['price']:.2f} ({s}{q['change']:.2f}, {s}{q['change_pct']:.2f}%)")
            else:
                lines.append(f"- {label}: データ取得失敗")
        return "\n".join(lines)

    def stock_rankings():
        res = []
        for sym, label in STOCK_SYMBOLS.items():
            q = fetch_from_stooq(sym)
            if q: res.append({"name": label, "pct": q["change_pct"]})
        if not res: return "■ 指定銘柄: データ取得に失敗しました。"
        res.sort(key=lambda x: x["pct"], reverse=True)
        ups = res[:3]; downs = sorted(res[-3:], key=lambda x: x["pct"])
        fmt = lambda e: f"{e['name']} ({'+' if e['pct']>=0 else ''}{e['pct']:.2f}%)"
        lines = ["■ 値上がり率 上位（指定銘柄）"] + [f"- {fmt(x)}" for x in ups]
        lines += ["■ 値下がり率 上位（指定銘柄）"] + [f"- {fmt(x)}" for x in downs]
        up = sum(1 for r in res if r["pct"] > 0); down = sum(1 for r in res if r["pct"] < 0)
        flat = len(res) - up - down
        lines += [f"■ 地合い（指定銘柄ベース）: 上昇 {up}, 下落 {down}, 横ばい {flat}"]
        return "\n".join(lines)

    parts = [head, "■ 主要指数", fmt_lines(INDEX_SYMBOLS), stock_rankings()]
    if GOLD_SYMBOLS:
        parts += ["■ 金価格など", fmt_lines(GOLD_SYMBOLS)]
    return "\n\n".join(parts)

def llm_answer(query: str, context: str = "") -> str:
    prompt = f"""あなたは日本のマーケットアシスタントです。
ユーザー質問: {query}
参考情報:
{context}

指示:
- 最初に結論（1-2行）
- 必要なら箇条書き（3-6行）
- チャートURLを最後に案内
- データはStooq由来。過度な断言は避ける
"""
    res = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role":"user","content":prompt}],
        temperature=0.2,
    )
    return res.choices[0].message.content.strip()

# ===== Slack ハンドラ（共通）=====
@slack_app.command("/market")
def cmd_market(ack, body, respond):
    ack()
    text = (body.get("text") or "").strip()
    if not text:
        respond(f"使い方例:\n• `/market 7203.JP`\n• `/market NKX`\n• `/market 今朝の要点`\n個別一覧: {pages_list()}")
        return

    import re
    syms = re.findall(r"([0-9]{4}\.JP|\^[A-Z]+|XAUUSD|XAUJPY)", text.upper())
    ctx_lines: List[str] = []
    for s in set(syms):
        q = fetch_from_stooq(s)
        if q:
            ctx_lines.append(f"{s}: {q['price']:.2f} ({q['change']:+.2f}, {q['change_pct']:+.2f}%), date={q['date']}\nチャート: {chart_url(s)}")
        else:
            ctx_lines.append(f"{s}: 取得失敗")

    if not ctx_lines and any(k in text for k in ["要点", "まとめ", "今朝"]):
        ctx = f"総合チャート: {pages_index()} / 個別一覧: {pages_list()}"
    else:
        ctx = "\n".join(ctx_lines) + f"\n総合チャート: {pages_index()} / 個別一覧: {pages_list()}"

    respond(llm_answer(text, ctx))

@slack_app.event("app_mention")
def on_mention(event, say):
    text = event.get("text","")
    say(llm_answer(text, f"総合: {pages_index()} / 一覧: {pages_list()}"))

# ===== 起動：Socket / HTTP 切替 =====
if RUN_MODE == "socket":
    # 公開URL不要
    assert SLACK_APP_TOKEN, "SLACK_APP_TOKEN(xapp-...) が必要です"
    from slack_bolt.adapter.socket_mode import SocketModeHandler
    if __name__ == "__main__":
        print("RUN_MODE=socket で起動")
        SocketModeHandler(slack_app, SLACK_APP_TOKEN).start()
else:
    # 公開URL必要
    assert SLACK_SIGNING_SECRET, "SLACK_SIGNING_SECRET が必要です"
    app = FastAPI()
    handler = SlackRequestHandler(slack_app)

    @app.post("/slack/events")
    async def slack_events(req: Request):
        return await handler.handle(req)

    if __name__ == "__main__":
        import uvicorn
        print("RUN_MODE=http で起動（/slack/events を公開しSlackに設定）")
        uvicorn.run(app, host="0.0.0.0", port=8000)
