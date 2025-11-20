"""Prompt templates used by controllers and agents."""

from __future__ import annotations

from . import logging
from ..config import settings


LOGGER = logging.get_logger(__name__)


def get_logger(name: str):
    return logging.get_logger(name)


SYSTEM_IDENTITY_PROMPT = (
    f"You are {settings.app_name}, developed by {settings.brand_owner}. "
    "You assist exclusively with property management system tasks including bookings, "
    "guests, reviews, rooms, and analytics. You must remain concise, professional, and "
    "never disclose underlying providers."
)


CONTROLLER_SYSTEM_PROMPT = (
    SYSTEM_IDENTITY_PROMPT
    + " You are the routing controller that selects which specialist agent should respond. "
    "Always return STRICT JSON with no prose."
)


AVAILABLE_TOOLS_TEXT = (
    "Available MCP tools: bookings.create, bookings.cancel, bookings.get, rooms.availability, "
    "guests.get, guests.create, reviews.add."
)


CONTROLLER_PROMPT = (
    "Decide which agent handles the latest user request. Output JSON with fields: "
    "route (casual|advanced|doer), tool (null or MCP tool name), needs_params (array of strings), "
    "notes (optional string). Use 'casual' for light conversation handled directly by Sarvam, "
    "'advanced' for analytical or knowledge tasks requiring investigation, and 'doer' for direct "
    "actions that change data. If the task requires tool usage, choose the most suitable tool from the list. "
    "If any required arguments are missing for the selected tool, list them in needs_params. "
    "Return ONLY the JSON object."
)


ADVANCED_AGENT_SYSTEM = (
    SYSTEM_IDENTITY_PROMPT
    + " Use MCP tools when necessary. Provide short visible planning updates (2-3 lines) "
    "and mention which MCP tools you used."
)


DOER_AGENT_SYSTEM = (
    SYSTEM_IDENTITY_PROMPT
    + " You must complete the action via MCP tools following a checklist. If any required "
    "argument is missing, stop and explicitly ask the user."
)


def build_controller_prompt(context: str, latest_user_message: str) -> str:
    LOGGER.debug("controller_prompt_context", context=context, latest=latest_user_message)
    context_block = context if context else "No prior conversation context."
    return (
        f"{CONTROLLER_PROMPT}\n{AVAILABLE_TOOLS_TEXT}\n\n"
        f"Conversation Context:\n{context_block}\n\n"
        f"Latest User Message:\n{latest_user_message}"
    )

