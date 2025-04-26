from logging import debug
from agno import tools
from dotenv  import load_dotenv
load_dotenv()

from agno.agent import Agent
from agno.models.openai import OpenAIChat
from agno.models.google import Gemini
from agno.models.openrouter import OpenRouter
from agno.models.groq import Groq
from agno.tools.googlesearch import GoogleSearchTools
from agno.storage.sqlite import SqliteStorage
from agno.utils.pprint import pprint_run_response
from agno.tools import FunctionCall, tool
from markitdown import MarkItDown

agent_storage = SqliteStorage(
    table_name="agent_sessions",
    db_file="tmp/persistent_memory.db",
)

human_summary_agent = Agent(
    name="human_summary_agent",
    description="You are a helpful assistant who can convert the given output of a tool into human readable text in markdown format.",
    # model=OpenAIChat(id="gpt-4o-mini"),
    model=Gemini(id="gemini-2.5-flash-preview-04-17"),  # or any other supported model
    # model=OpenRouter(id="qwen/qwq-32b"),  # or any other supported model
    # model=Groq(id="llama-3.3-70b-versatile"),  # or any other supported model
    instructions=[
        "Do not alter the contents / intent of output.",
        "Just return the contents into a markdown format. DO NOT ADD / SAY ANYTHING ELSE",
    ],
)

GoogleSearchToolKit = GoogleSearchTools()

def google_search(
    agent: Agent,
    query: str,
    max_results: int = 5,
    language: str = "en"
):
    """
    Use this function to search Google for a specified query.

    Args:
        query (str): The query to search for.
        max_results (int, optional): The maximum number of results to return. Default is 5.
        language (str, optional): The language of the search results. Default is "en".

    Returns:
        str: A JSON formatted string containing the search results.
    """
    json_string = GoogleSearchToolKit.google_search(query, max_results, language)
    response = human_summary_agent.run(f"Convert the following output into human readable text in markdown format: {json_string}")
    response = response.content
    # md = MarkItDown(enable_plugins=False) # Set to True to enable plugins
    # result = md.convert_response(json_string)
    # response = result.markdown
    # print(result.text_content)
    print(f">>>>>>>>>>>>>>>>>>>> Human Summary Agent Response <<<<<<<<<<< : {response}")
    if agent.session_state is None:
        agent.session_state = {}
    agent.session_state['last_tool_call_response'] = response
    return json_string

sample_agent = Agent(
    name="math_agno_agent",
    description="You are a helpful assistant who can solve math problems.",
    # model=OpenAIChat(id="gpt-4o-mini"),
    model=Gemini(id="gemini-2.5-flash-preview-04-17"),  # or any other supported model
    # model=OpenRouter(id="qwen/qwq-32b"),  # or any other supported model
    # model=Groq(id="llama-3.3-70b-versatile"),  # or any other supported model
    instructions=[
        "If user input is not sufficent, ask user relevant questions / clarifications",
        "solve problems step by steps to find the answer. do not rush."
    ],
    storage=agent_storage,
    # add_history_to_messages=True,
    # num_history_responses=5,
    tools=[
        # GoogleSearchTools()
        google_search
    ],
    # reasoning=True,
    stream_intermediate_steps=True,
    add_datetime_to_instructions=True,
    debug_mode=True
)

# response = sample_agent.run("A chemist has a 10-liter solution that is 30% alcohol. How much pure alcohol must she add to make the solution 50% alcohol?", stream=True)
# pprint_run_response(response, markdown=True)

# response = sample_agent.print_response("A chemist has a 10-liter solution that is 30% alcohol. How much pure alcohol must she add to make the solution 50% alcohol?", stream=True)
