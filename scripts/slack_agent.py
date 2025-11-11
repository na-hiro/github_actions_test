import os
from dotenv import load_dotenv
from langchain_community.agent_toolkits import SlackToolkit
from langchain_openai import ChatOpenAI  # ← こちら推奨（古い書き方でも動くことは多い）
from langchain.agents import AgentType, initialize_agent

load_dotenv()

assert os.getenv("OPENAI_API_KEY"), "OPENAI_API_KEY が設定されていません"
assert os.getenv("SLACK_USER_TOKEN"), "SLACK_USER_TOKEN が設定されていません"

toolkit = SlackToolkit()
tools = toolkit.get_tools()

llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.5)

agent_executor = initialize_agent(
    tools=tools,
    llm=llm,
    agent=AgentType.STRUCTURED_CHAT_ZERO_SHOT_REACT_DESCRIPTION,
    verbose=True,
)

result = agent_executor.run("「all-動作検証用」チャンネルに「Test」と送信して")
print(result)
