from dotenv  import load_dotenv
load_dotenv()

from agno.agent import Agent
from agno.models.openai import OpenAIChat
from agno.models.google import Gemini
from agno.tools.googlesearch import GoogleSearchTools
from agno.tools.yfinance import YFinanceTools
from agno.tools.crawl4ai import Crawl4aiTools
from agno.storage.sqlite import SqliteStorage

agent_storage = SqliteStorage(
    table_name="agent_sessions",
    db_file="tmp/persistent_memory.db",
)

sample_agent = Agent(
    name="agno_agent",
    description="You are a helpful assistant who can search web, crawl page contents and also search financial information using given tools to achieve the given user goal.",
    # model=OpenAIChat(id="gpt-4o-mini"),
    model=Gemini(id="gemini-2.5-flash-preview-04-17"),  # or any other supported model
    instructions=[
        "If user input is not sufficent, ask user relevant questions / clarifications",
        "To search the web, use google search tool.",
        "To crawl the pages use crawl4ai tools",
        "To get financial information about companies, use YFinance tools"
    ],
    storage=agent_storage,
    tools=[
        GoogleSearchTools(),
        YFinanceTools(enable_all=True),
        Crawl4aiTools(max_length=5000)
    ],
    stream_intermediate_steps=True,
    add_datetime_to_instructions=True,
    debug_mode=True
)

user_prompt = "whats the latest news about China"
for res in sample_agent.run(user_prompt, stream=True):
    print(f"Response Type: {res.event}")
    print(f"Tools: {res.tools}")
    # print(f"Messages: {res.messages}")