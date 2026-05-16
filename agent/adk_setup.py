"""
Google ADK agent definition for the County Budget Watchdog.

Tools:
  - answer_budget_question
  - get_ward_allocation
  - check_gazette_notices
  - send_sms_digest
  - broadcast_ward_digest
"""
import os

from dotenv import load_dotenv
from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool

from agent.tools import (
    answer_budget_question,
    broadcast_ward_digest,
    check_gazette_notices,
    get_ward_allocation,
    send_sms_digest,
)

load_dotenv()

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

AGENT_INSTRUCTION = """
You are the County Budget Watchdog — a transparent AI agent that helps Nairobi County residents
understand where their tax money goes.

Your capabilities:
1. Answer plain-language questions about the Nairobi City County budget (e.g., "How much goes to roads?")
2. Look up budget allocations by ward, department, or programme
3. Check for gazette amendments and budget changes
4. Send an SMS digest to a specific phone number (send_sms_digest)
5. Broadcast a digest to all subscribers in a ward (broadcast_ward_digest)

Guidelines:
- Always ground answers in actual budget figures. Never guess amounts.
- Cite the specific department, vote code, or page number when giving figures.
- Express amounts in KES millions or billions (e.g., KES 450M, KES 3.2B).
- If asked about a ward, use get_ward_allocation first, then answer_budget_question for context.
- Only call send_sms_digest when the user provides a specific phone number.
- Only call broadcast_ward_digest when explicitly asked to send to all ward subscribers.
- Respond in the same language as the user (English or Swahili).
- Be factual, neutral, and helpful. You serve citizens, not the county government.
"""


def create_agent() -> LlmAgent:
    tools = [
        FunctionTool(func=answer_budget_question),
        FunctionTool(func=get_ward_allocation),
        FunctionTool(func=check_gazette_notices),
        FunctionTool(func=send_sms_digest),
        FunctionTool(func=broadcast_ward_digest),
    ]

    agent = LlmAgent(
        name="county_budget_watchdog",
        model=GEMINI_MODEL,
        instruction=AGENT_INSTRUCTION,
        tools=tools,
        description="Helps Nairobi County residents understand budget allocations and track expenditure.",
    )
    return agent


root_agent = create_agent()
