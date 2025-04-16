from logging import debug
from dotenv  import load_dotenv
load_dotenv()

from agno.agent import Agent
from agno.models.openai import OpenAIChat
from agno.models.google import Gemini
from agno.tools.googlesearch import GoogleSearchTools
from agno.storage.sqlite import SqliteStorage

agent_storage = SqliteStorage(
    table_name="agent_sessions",
    db_file="tmp/persistent_memory.db",
)

sample_agent = Agent(
    name="math_agno_agent",
    description="You are a helpful assistant who can solve math problems.",
    model=OpenAIChat(id="gpt-4o-mini"),
    # model=Gemini(id="gemini-2.0-flash-exp"),  # or any other supported model
    instructions=[
        "If user input is not sufficent, ask user relevant questions / clarifications",
        "solve problems step by steps to find the answer. do not rush."
    ],
    storage=agent_storage,
    # add_history_to_messages=True,
    # num_history_responses=5,
    tools=[GoogleSearchTools()],
    stream_intermediate_steps=True,
    add_datetime_to_instructions=True,
    # debug_mode=True
)
