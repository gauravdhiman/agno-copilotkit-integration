from dotenv  import load_dotenv
load_dotenv()

from agno.agent import Agent
from agno.models.openai import OpenAIChat
from agno.tools.googlesearch import GoogleSearchTools


sample_agent = Agent(
    name="finance_agno_agent",
    description="You are a helpful assistant who can solve math problems.",
    model=OpenAIChat(id="gpt-4o-mini"),
    # model=Gemini(id="gemini-2.0-flash-exp"),  # or any other supported model
    instructions=[
        "If user input is not sufficent, ask user relevant questions / clarifications",
        "solve problems step by steps to find the answer. do not rush."
    ],
    tools=[GoogleSearchTools()],
    stream_intermediate_steps=True
)
