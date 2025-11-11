#!/usr/bin/env python3
import os
import datetime

from dotenv import load_dotenv
from langchain_community.agent_toolkits import SlackToolkit
from langchain_openai import ChatOpenAI
from langchain.agents import AgentType, initialize_agent

load_dotenv()

# 必須環境変数チェック
assert os.getenv("OPENAI_API_KEY"), "OPENAI_API_KEY が設定されていません"
assert os.getenv("SLACK_USER_TOKEN"), "SLACK_USER_TOKEN が設定されていません"

def build_prompt() -> str:
    """実データなしで、市場サマリー風テキストを生成させるためのプロンプト"""
    jst = datetime.datetime.utcnow() + datetime.timedelta(hours=9)
    date_str = jst.strftime("%Y-%m-%d")

    return f"""
今日は {date_str} です。

実際の株価データやニュースにはアクセスせずに、
一般的によくある値動きパターンをベースにした
「サンプル的な株式市場サマリー」を日本語で作成してください。

条件:
- 世界の主要市場（日本、米国、欧州など）について触れる
- 3〜6行程度の箇条書き
- 「〜といった動きが見られました」など、例示的・仮想的な表現にする
- 具体的な指数値・騰落率は、あくまで例として自然な数字を使ってよいが、
  「実際のデータに基づくものではありません」と分かる書き方にする
- 最後に必ず次の一文を入れる：
  「※このサマリーは自動生成されたサンプルであり、実際の市場データに基づくものではありません。」

出力は、そのままSlackに投稿できる文章だけを返してください。
"""

def main():
    # Slack ツールと LLM 初期化
    toolkit = SlackToolkit()
    tools = toolkit.get_tools()

    llm = ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0.4,
    )

    agent_executor = initialize_agent(
        tools=tools,
        llm=llm,
        agent=AgentType.STRUCTURED_CHAT_ZERO_SHOT_REACT_DESCRIPTION,
        verbose=True,
    )

    prompt = build_prompt()

    # SlackToolkit に「指定チャンネルへこのサマリを投稿させる」指示
    command = f"""
「all-動作検証用」チャンネルに、以下のテキストを投稿してください：

{prompt}
"""

    result = agent_executor.run(command)
    print("Agent result:", result)

if __name__ == "__main__":
    main()
