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
import json  # ← 追加: /history 用に JSON を読む

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

# ===== /history 用：過去レポート読み込み & 検索 =====

def load_history_reports():
    """
    slack_agent.py が保存した reports/*.json を全部読む。
    戻り値: List[dict] （date_jst, snapshot, summary, ...）
    """
    reports_dir = Path(__file__).parent / "reports"
    if not reports_dir.exists():
        print("[history] reports ディレクトリがありません:", reports_dir)
        return []

    reports = []
    for p in sorted(reports_dir.glob("*.json")):
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
            # date_jst が無い場合に備えてファイル名も持っておく
            j["_filename"] = p.name
            reports.append(j)
        except Exception as e:
            print(f"[history] {p} の読み込みでエラー:", e)
    return reports

def search_history_reports(query: str, limit: int = 5):
    """
    簡易版: テキストに query が含まれているレポートを後ろから最大 limit 件取る。
    query が空なら、単に最近のレポートを limit 件返す。
    """
    all_reports = load_history_reports()
    if not all_reports:
        return []

    if not query.strip():
        # そのまま末尾から limit 件（新しいもの想定）
        return all_reports[-limit:]

    q = query.strip()
    hits = []
    for r in all_reports:
        text = (r.get("summary") or "") + "\n" + (r.get("snapshot") or "")
        if q in text:
            hits.append(r)

    if not hits:
        return []

    # 新しい方から limit 件
    return hits[-limit:]

def llm_history_answer(question: str, history_context: str) -> str:
    """
    /history 用の回答生成。
    history_context には過去レポートの要約＋日付が入っている前提。
    """
    prompt = f"""あなたは日本のマーケットアシスタントです。
ユーザーから、過去のマーケットレポートにもとづく質問が来ています。

ユーザーの質問:
{question}

以下は、過去数日分のマーケットレポート（スナップショット＋サマリー）です：
{history_context}

指示:
- 過去レポートに書かれている範囲でだけ答えてください。
- レポートに無いことは推測せず、「レポートからは分かりません」と正直に言ってください。
- 日付（いつ頃）と内容の対応がわかるように説明してください。
- 先頭に結論、そのあとに箇条書きで整理してください。
- 最後に必ず次の一文を付けてください：
  「※この回答は過去の自動生成レポートを元にした参考情報であり、正確性・完全性・将来の成果を保証するものではありません。」
"""
    res = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
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

# ==== /history コマンド ====

@slack_app.command("/history")
def cmd_history(ack, body, respond):
    """
    例: /history 最近の半導体関連銘柄の動きを教えて
         /history ここ1週間で地合いが悪かった日は？
    """
    ack()
    text = (body.get("text") or "").strip()

    if not text:
        respond("使い方例:\n• `/history 最近の半導体テーマの動きは？`\n• `/history ここ1週間で地合いが悪かった日は？`")
        return

    reports = search_history_reports(text, limit=5)
    if not reports:
        respond("過去レポート（reports/*.json）から該当するものが見つかりませんでした。`slack_agent.py` が JSON を保存しているか、GitHub から最新を pull できているか確認してください。")
        return

    # LLM に渡すコンテキストを組み立てる
    ctx_lines = []
    for r in reports:
        date_str = r.get("date_jst") or r.get("_filename", "unknown")
        summary  = r.get("summary")  or ""
        snapshot = r.get("snapshot") or ""
        ctx_lines.append(f"【{date_str} のレポート】\nサマリー:\n{summary}\n\nスナップショット:\n{snapshot}\n")

    history_ctx = "\n\n".join(ctx_lines)
    answer = llm_history_answer(text, history_ctx)
    respond(answer)

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
