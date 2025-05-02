from agno.agent import Agent
from agno.models.google import Gemini

markdown_agent = Agent(
    name="agno_agent",
    description="You are a helpful assistant who converts the given text into well formatted mardown content.",
    model=Gemini(id="gemini-2.5-flash-preview-04-17"),  # or any other supported model
)