from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Literal, Optional, Tuple

# Try to use zoneinfo (Python 3.9+), fallback to pytz if needed
USE_PYTZ = False
INDIA_TZ = None
try:
    from zoneinfo import ZoneInfo
except ImportError:
    try:
        from backports.zoneinfo import ZoneInfo
    except ImportError:
        import pytz
        USE_PYTZ = True
        # Store pytz timezone for India
        INDIA_TZ = pytz.timezone("Asia/Kolkata")

from ..models.chat import ChatRequest, Message
from ..services import event_bus, llm_sarvam, token_storage
from ..mcp import tools as mcp_tools
from ..config import settings

import inspect
from typing import Callable


# Conversation state tracker for handling follow-up queries
# Maps session_id -> pending request context
_conversation_state: Dict[str, Dict[str, Any]] = {}


def _get_conversation_state(sid: str) -> Optional[Dict[str, Any]]:
    """Get pending conversation state for a session."""
    return _conversation_state.get(sid)


def _set_conversation_state(sid: str, state: Dict[str, Any]) -> None:
    """Set conversation state for a session."""
    _conversation_state[sid] = state


def _clear_conversation_state(sid: str) -> None:
    """Clear conversation state for a session."""
    _conversation_state.pop(sid, None)


async def _is_topic_shift(messages: List[Message], pending_state: Dict[str, Any]) -> bool:
    """
    Detect if the user's query is a topic shift (new query) vs a follow-up to pending state.
    Returns True if it's a topic shift (should clear pending state), False if it's a follow-up.
    """
    if not messages:
        return False
    
    # Get the latest user message
    latest_message = messages[-1].content.lower() if messages else ""
    
    # Get pending context
    pending_tool_group = pending_state.get("tool_group", "")
    pending_tool = pending_state.get("tool", "")
    
    # Keywords that indicate a completely new topic (not a follow-up)
    new_topic_keywords = {
        "billing_finance": ["bill", "invoice", "payment", "folio", "charge", "billing", "finance", "account"],
        "frontoffice": ["arrival", "departure", "reservation", "booking", "check-in", "check-out", "guest"],
        "guest_management": ["guest", "profile", "review", "reputation"],
        "distribution": ["promotion", "rate", "inventory", "discount", "offer"],
        "foundation": ["room", "room type", "room type"],
        "communication": ["conversation", "message", "campaign", "chat"],
        "settings": ["setting", "config", "preference"]
    }
    
    # Check if the latest message contains keywords from a different tool group
    for group, keywords in new_topic_keywords.items():
        if group != pending_tool_group:
            # If user mentions keywords from a different group, it's likely a topic shift
            if any(keyword in latest_message for keyword in keywords):
                return True
    
    # If the message is very short and seems like a direct answer (date, number, yes/no), it's likely a follow-up
    if len(latest_message.split()) <= 5 and any(char.isdigit() for char in latest_message):
        return False
    
    # If message contains "can you", "give me", "show me", "get me" - likely a new query
    new_query_indicators = ["can you", "give me", "show me", "get me", "i need", "i want", "please"]
    if any(indicator in latest_message for indicator in new_query_indicators):
        return True
    
    # Default: assume it's a follow-up if we're not sure
    return False


def _get_language_instruction() -> str:
    """
    Returns a concise prompt instruction for the LLM to detect and match the user's language.
    """
    return (
        "LANGUAGE & SCRIPT RULE:\n"
        "Detect the user's language and script (e.g., English, Hindi, Hinglish, Tamil, etc.) from their message. "
        "Respond in the EXACT SAME language and script. "
        "If they use Romanized Hindi (Hinglish), you MUST use Romanized Hindi. "
        "If they use Devanagari Hindi, you MUST use Devanagari Hindi. "
        "Do not switch languages unless requested."
    )


def _get_current_datetime_info() -> str:
    """Get current date and time information for LLM context using India timezone (IST)."""
    # Use India timezone (Asia/Kolkata - IST, UTC+5:30)
    if USE_PYTZ and INDIA_TZ:
        # Use pytz for older Python versions
        now = datetime.now(INDIA_TZ)
    else:
        # Use zoneinfo (Python 3.9+)
        india_tz = ZoneInfo("Asia/Kolkata")
        now = datetime.now(india_tz)
    
    # Format: ISO date (YYYY-MM-DD), human-readable date, and time
    iso_date = now.strftime("%Y-%m-%d")
    human_date = now.strftime("%B %d, %Y")  # e.g., "November 18, 2025"
    time_str = now.strftime("%H:%M:%S IST")
    day_name = now.strftime("%A")  # e.g., "Monday"
    
    # Calculate tomorrow and yesterday using timedelta
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    
    return (
        f"CURRENT DATE AND TIME (India Standard Time - IST):\n"
        f"- ISO Date (YYYY-MM-DD): {iso_date}\n"
        f"- Human-readable: {human_date} ({day_name})\n"
        f"- Time: {time_str}\n"
        f"- When user says 'today', use date: {iso_date}\n"
        f"- When user says 'tomorrow', use date: {tomorrow}\n"
        f"- When user says 'yesterday', use date: {yesterday}\n"
    )


# Tool Groups and their purposes
TOOL_GROUPS = {
    "frontoffice": {
        "name": "Front Office",
        "description": "Handles reservations, check-ins, check-outs, departures, and guest room assignments. Used for managing bookings, viewing reservation details, checking availability, and handling day-to-day hotel operations.",
        "use_cases": [
            "Creating, updating, or deleting reservations",
            "Viewing reservation details and lists",
            "Checking room availability",
            "Viewing departures for a specific date",
            "Managing guest room assignments"
        ]
    },
    "billing_finance": {
        "name": "Billing & Finance",
        "description": "Manages guest folios, charges, payments, and financial transactions. Used for creating bills, adding charges, processing payments, updating folios, and handling checkout processes.",
        "use_cases": [
            "Creating guest folios",
            "Adding charges to folios",
            "Processing payments",
            "Updating folio information",
            "Handling checkout and billing"
        ]
    },
    "guest_management": {
        "name": "Guest Management",
        "description": "Manages guest profiles, guest information, reviews, and reputation. Used for creating/updating guest records, viewing guest lists, managing reviews, and handling guest-related data.",
        "use_cases": [
            "Creating or updating guest profiles",
            "Viewing guest lists and details",
            "Managing guest reviews",
            "Adding guests to reservations",
            "Summarizing reviews"
        ]
    },
    "distribution": {
        "name": "Distribution & Revenue",
        "description": "Manages promotions, rates, inventory blocks, and availability. Used for creating promotions, setting rates, blocking/unblocking inventory, and viewing availability reports.",
        "use_cases": [
            "Creating, updating, or deleting promotions",
            "Setting and viewing rates",
            "Blocking or unblocking inventory",
            "Viewing inventory availability",
            "Managing distribution channels"
        ]
    },
    "foundation": {
        "name": "Foundation & Rooms",
        "description": "Manages room types, physical rooms, room status, and housekeeping. Used for viewing room lists, room types, checking room details, blocking/unblocking rooms, setting room status (maintenance, dirty, clean, out-of-order), and managing room inventory.",
        "use_cases": [
            "Viewing room lists and details",
            "Blocking or unblocking rooms",
            "Setting room status (maintenance, dirty, clean, available, occupied, out-of-order)",
            "Housekeeping operations",
            "Room maintenance management",
            "Checking room types",
            "Managing room inventory",
            "Viewing room availability"
        ]
    },
    "communication": {
        "name": "Communication",
        "description": "Manages guest conversations, messages, and marketing campaigns. Used for viewing conversations, sending messages, creating campaigns, and managing guest communications.",
        "use_cases": [
            "Viewing conversations and messages",
            "Sending messages to guests",
            "Creating marketing campaigns",
            "Marking messages as read"
        ]
    },
    "settings": {
        "name": "Settings",
        "description": "Manages system settings and AI configuration. Used for viewing and updating system settings, AI preferences, and configuration options.",
        "use_cases": [
            "Viewing AI settings",
            "Updating system configuration",
            "Managing preferences"
        ]
    }
}


# Comprehensive tools metadata with groups, descriptions, and parameters
TOOLS_METADATA: List[Dict[str, Any]] = [
    # Front Office Tools
    {
        "name": "reservation_get",
        "group": "frontoffice",
        "function": mcp_tools.reservation_get,
        "description": "Retrieves detailed information about a specific reservation by reservation ID. Use this when the user asks about a particular booking or reservation details.",
        "params": {
            "reservation_id": {"type": "string", "required": True, "description": "The unique identifier of the reservation"}
        },
        "operation": "read"
    },
    {
        "name": "reservations_list",
        "group": "frontoffice",
        "function": mcp_tools.reservations_list,
        "description": "Lists reservations with pagination (default: 15 per page). Use this when the user asks to see reservations, bookings, or reservation list. IMPORTANT: Automatically limits to 15 results per page to avoid token overload. If user asks for 'all reservations', this will return the first page.",
        "params": {
            "params": {"type": "object", "required": False, "description": "Query parameters: page (default 1), limit (default 15), status, search"}
        },
        "operation": "read"
    },
    {
        "name": "reservations_list_all",
        "group": "frontoffice",
        "function": mcp_tools.reservations_list_all,
        "description": "Retrieves all reservations without filtering. Use this when the user asks for all bookings or a complete reservation list.",
        "params": {},
        "operation": "read"
    },
    {
        "name": "reservations_departures",
        "group": "frontoffice",
        "function": mcp_tools.reservations_departures,
        "description": "Gets all departures (check-outs) for a specific date. Use this when the user asks about departures, check-outs, or who is leaving on a particular date.",
        "params": {
            "date_iso": {"type": "string", "required": True, "description": "Date in ISO format (YYYY-MM-DD) for which to get departures"}
        },
        "operation": "read"
    },
    {
        "name": "reservations_arrivals",
        "group": "frontoffice",
        "function": mcp_tools.reservations_arrivals,
        "description": "Gets all arrivals (check-ins) for a specific date. Use this when the user asks about arrivals, check-ins, or who is arriving on a particular date.",
        "params": {
            "date_iso": {"type": "string", "required": True, "description": "Date in ISO format (YYYY-MM-DD) for which to get arrivals"}
        },
        "operation": "read"
    },
    {
        "name": "check_availability",
        "group": "frontoffice",
        "function": mcp_tools.check_availability,
        "description": "Checks room availability for given dates and room type. Use this when the user asks about availability, if rooms are available, wants to check for specific dates, or asks 'aaj ki availability' (today's availability).",
        "params": {
            "payload": {
                "type": "object",
                "required": True,
                "description": "Object containing check-in date, check-out date, and room type ID",
                "nested_fields": {
                    "checkInDate": {"type": "string", "required": True, "description": "Check-in date in ISO format (YYYY-MM-DD). If user says 'today' or 'aaj', use today's date."},
                    "checkOutDate": {"type": "string", "required": True, "description": "Check-out date in ISO format (YYYY-MM-DD). If user says 'today's availability' without specifying checkout, use tomorrow's date (checkInDate + 1 day)."},
                    "roomTypeId": {"type": "string", "required": True, "description": "Room type ObjectId to check availability for. If user provides room type name (e.g., 'Platinum Cottage'), extract the name and the system will convert it to ID."}
                }
            }
        },
        "operation": "read"
    },
    {
        "name": "reservation_create",
        "group": "frontoffice",
        "function": mcp_tools.reservation_create,
        "description": "Creates a new reservation/booking. Use this when the user wants to make a booking, create a reservation, or book a room. REQUIRED fields: guestName (string), guestEmail (string), guestNumber/guestPhone (string), checkInDate (ISO date string YYYY-MM-DD), checkOutDate (ISO date string YYYY-MM-DD), roomType (ObjectId string - REQUIRED), totalGuest (number), mealPlan (string: 'EP', 'CP', 'MAP', or 'AP'). Optional fields: numberOfRooms (number), totalAmount (number), paidAmount (number), paymentMethod (string), notes (string).",
        "params": {
            "payload": {
                "type": "object",
                "required": True,
                "description": "Reservation payload object with nested fields",
                "nested_fields": {
                    "guestName": {"type": "string", "required": True, "description": "Guest's full name"},
                    "guestEmail": {"type": "string", "required": True, "description": "Guest's email address"},
                    "guestNumber": {"type": "string", "required": True, "description": "Guest's phone number (can also be guestPhone)"},
                    "checkInDate": {"type": "string", "required": True, "description": "Check-in date in ISO format (YYYY-MM-DD)"},
                    "checkOutDate": {"type": "string", "required": True, "description": "Check-out date in ISO format (YYYY-MM-DD)"},
                    "roomType": {"type": "string", "required": True, "description": "Room type ObjectId (REQUIRED - must be a valid room type ID)"},
                    "totalGuest": {"type": "number", "required": True, "description": "Total number of guests"},
                    "mealPlan": {"type": "string", "required": True, "description": "Meal plan code: 'EP' (European Plan/No meals), 'CP' (Continental Plan/Breakfast), 'MAP' (Modified American Plan/Breakfast+Dinner), 'AP' (American Plan/All meals)"},
                    "numberOfRooms": {"type": "number", "required": False, "description": "Number of rooms (optional)"},
                    "totalAmount": {"type": "number", "required": False, "description": "Total reservation amount (optional)"},
                    "paidAmount": {"type": "number", "required": False, "description": "Amount already paid (optional)"},
                    "paymentMethod": {"type": "string", "required": False, "description": "Payment method (e.g., 'Cash', 'UPI', 'Card')"},
                    "notes": {"type": "string", "required": False, "description": "Additional notes or special requests"}
                }
            }
        },
        "operation": "write"
    },
    {
        "name": "reservation_update",
        "group": "frontoffice",
        "function": mcp_tools.reservation_update,
        "description": "Updates an existing reservation. Use this when the user wants to modify a booking, change dates, update guest info, or alter reservation details.",
        "params": {
            "reservation_id": {"type": "string", "required": True, "description": "The unique identifier of the reservation to update"},
            "payload": {
                "type": "object",
                "required": True,
                "description": "Updated reservation data with fields to update",
                "nested_fields": {
                    "guestName": {"type": "string", "required": False, "description": "Guest's full name"},
                    "guestEmail": {"type": "string", "required": False, "description": "Guest's email address"},
                    "guestNumber": {"type": "string", "required": False, "description": "Guest's phone number"},
                    "checkInDate": {"type": "string", "required": False, "description": "Check-in date in ISO format (YYYY-MM-DD)"},
                    "checkOutDate": {"type": "string", "required": False, "description": "Check-out date in ISO format (YYYY-MM-DD)"},
                    "roomType": {"type": "string", "required": False, "description": "Room type ObjectId"},
                    "totalGuest": {"type": "number", "required": False, "description": "Total number of guests"},
                    "mealPlan": {"type": "string", "required": False, "description": "Meal plan code: 'EP', 'CP', 'MAP', or 'AP'"},
                    "numberOfRooms": {"type": "number", "required": False, "description": "Number of rooms"},
                    "totalAmount": {"type": "number", "required": False, "description": "Total reservation amount"},
                    "paidAmount": {"type": "number", "required": False, "description": "Amount already paid"},
                    "paymentMethod": {"type": "string", "required": False, "description": "Payment method"},
                    "status": {"type": "string", "required": False, "description": "Reservation status"},
                    "notes": {"type": "string", "required": False, "description": "Additional notes"}
                }
            }
        },
        "operation": "write"
    },
    {
        "name": "reservation_delete",
        "group": "frontoffice",
        "function": mcp_tools.reservation_delete,
        "description": "Cancels or deletes a reservation. Use this when the user wants to cancel a booking or remove a reservation.",
        "params": {
            "reservation_id": {"type": "string", "required": True, "description": "The unique identifier of the reservation to delete"}
        },
        "operation": "write"
    },
    # Billing & Finance Tools
    {
        "name": "folios_list",
        "group": "billing_finance",
        "function": mcp_tools.folios_list,
        "description": "Lists folios/bills with pagination (default: 15 per page). Use this when the user asks to see bills, folios, or billing list. IMPORTANT: Automatically limits to 15 results per page to avoid token overload.",
        "params": {
            "params": {"type": "object", "required": False, "description": "Query parameters: page (default 1), limit (default 15)"}
        },
        "operation": "read"
    },
    {
        "name": "folio_get",
        "group": "billing_finance",
        "function": mcp_tools.folio_get,
        "description": "Retrieves detailed information about a specific folio (bill) by folio ID. Use this when the user asks about a particular bill or folio details.",
        "params": {
            "folio_id": {"type": "string", "required": True, "description": "The unique identifier of the folio"}
        },
        "operation": "read"
    },
    {
        "name": "folio_create",
        "group": "billing_finance",
        "function": mcp_tools.folio_create,
        "description": "Creates a new folio (bill) for a guest. Use this when the user wants to create a new bill or folio.",
        "params": {
            "payload": {
                "type": "object",
                "required": True,
                "description": "Folio data including guest ID, reservation ID, etc.",
                "nested_fields": {
                    "guestId": {"type": "string", "required": True, "description": "Guest ObjectId (REQUIRED)"},
                    "reservationId": {"type": "string", "required": False, "description": "Reservation ObjectId (optional)"},
                    "notes": {"type": "string", "required": False, "description": "Additional notes"}
                }
            }
        },
        "operation": "write"
    },
    {
        "name": "folio_add_charge",
        "group": "billing_finance",
        "function": mcp_tools.folio_add_charge,
        "description": "Adds a charge to an existing folio. Use this when the user wants to add a charge, fee, or expense to a bill.",
        "params": {
            "folio_id": {"type": "string", "required": True, "description": "The unique identifier of the folio"},
            "payload": {
                "type": "object",
                "required": True,
                "description": "Charge data including amount, description, category, etc.",
                "nested_fields": {
                    "amount": {"type": "number", "required": True, "description": "Charge amount (REQUIRED)"},
                    "description": {"type": "string", "required": True, "description": "Description of the charge (REQUIRED)"},
                    "category": {"type": "string", "required": False, "description": "Charge category (e.g., 'Room', 'Food', 'Service')"},
                    "date": {"type": "string", "required": False, "description": "Charge date in ISO format (YYYY-MM-DD)"}
                }
            }
        },
        "operation": "write"
    },
    {
        "name": "folio_add_payment",
        "group": "billing_finance",
        "function": mcp_tools.folio_add_payment,
        "description": "Adds a payment to an existing folio. Use this when the user wants to record a payment, process payment, or add payment to a bill.",
        "params": {
            "folio_id": {"type": "string", "required": True, "description": "The unique identifier of the folio"},
            "payload": {
                "type": "object",
                "required": True,
                "description": "Payment data including amount, payment method, etc.",
                "nested_fields": {
                    "amount": {"type": "number", "required": True, "description": "Payment amount (REQUIRED)"},
                    "paymentMethod": {"type": "string", "required": True, "description": "Payment method (e.g., 'Cash', 'UPI', 'Card', 'Bank Transfer') (REQUIRED)"},
                    "date": {"type": "string", "required": False, "description": "Payment date in ISO format (YYYY-MM-DD)"},
                    "reference": {"type": "string", "required": False, "description": "Payment reference number or transaction ID"},
                    "notes": {"type": "string", "required": False, "description": "Additional payment notes"}
                }
            }
        },
        "operation": "write"
    },
    {
        "name": "folio_update",
        "group": "billing_finance",
        "function": mcp_tools.folio_update,
        "description": "Updates an existing folio. Use this when the user wants to modify folio information or bill details.",
        "params": {
            "folio_id": {"type": "string", "required": True, "description": "The unique identifier of the folio"},
            "payload": {
                "type": "object",
                "required": True,
                "description": "Updated folio data",
                "nested_fields": {
                    "guestId": {"type": "string", "required": False, "description": "Guest ObjectId"},
                    "reservationId": {"type": "string", "required": False, "description": "Reservation ObjectId"},
                    "status": {"type": "string", "required": False, "description": "Folio status"},
                    "notes": {"type": "string", "required": False, "description": "Additional notes"}
                }
            }
        },
        "operation": "write"
    },
    {
        "name": "folio_checkout",
        "group": "billing_finance",
        "function": mcp_tools.folio_checkout,
        "description": "Processes checkout for a folio. Use this when the user wants to checkout a guest, finalize a bill, or complete checkout.",
        "params": {
            "folio_id": {"type": "string", "required": True, "description": "The unique identifier of the folio to checkout"}
        },
        "operation": "write"
    },
    # Guest Management Tools
    {
        "name": "guests_list",
        "group": "guest_management",
        "function": mcp_tools.guests_list,
        "description": "Lists guests with pagination (default: 15 per page). Use this when the user asks to see guests, guest list, or all guest profiles. Automatically defaults to recent guests (page 1, limit 15) to avoid token overload.",
        "params": {
            "params": {"type": "object", "required": False, "description": "Query parameters: page (default 1), limit (default 15), search, reservationId"}
        },
        "operation": "read"
    },
    {
        "name": "guest_create_or_update",
        "group": "guest_management",
        "function": mcp_tools.guest_create_or_update,
        "description": "Creates a new guest profile or updates an existing one if found by email/phone. Use this when the user wants to add a guest or update guest information.",
        "params": {
            "payload": {
                "type": "object",
                "required": True,
                "description": "Guest data including name, email, phone, address, etc.",
                "nested_fields": {
                    "name": {"type": "string", "required": True, "description": "Guest's full name (REQUIRED)"},
                    "email": {"type": "string", "required": True, "description": "Guest's email address (REQUIRED)"},
                    "phone": {"type": "string", "required": True, "description": "Guest's phone number (REQUIRED)"},
                    "address": {"type": "object", "required": False, "description": "Guest's address object"},
                    "dateOfBirth": {"type": "string", "required": False, "description": "Date of birth in ISO format (YYYY-MM-DD)"},
                    "nationality": {"type": "string", "required": False, "description": "Guest's nationality"},
                    "idType": {"type": "string", "required": False, "description": "ID type (e.g., 'Passport', 'Aadhaar', 'Driving License')"},
                    "idNumber": {"type": "string", "required": False, "description": "ID number"},
                    "notes": {"type": "string", "required": False, "description": "Additional notes"}
                }
            }
        },
        "operation": "write"
    },
    {
        "name": "guest_update",
        "group": "guest_management",
        "function": mcp_tools.guest_update,
        "description": "Updates an existing guest profile by guest ID. Use this when the user wants to modify guest information for a specific guest.",
        "params": {
            "guest_id": {"type": "string", "required": True, "description": "The unique identifier of the guest"},
            "payload": {
                "type": "object",
                "required": True,
                "description": "Updated guest data",
                "nested_fields": {
                    "name": {"type": "string", "required": False, "description": "Guest's full name"},
                    "email": {"type": "string", "required": False, "description": "Guest's email address"},
                    "phone": {"type": "string", "required": False, "description": "Guest's phone number"},
                    "address": {"type": "object", "required": False, "description": "Guest's address object"},
                    "dateOfBirth": {"type": "string", "required": False, "description": "Date of birth in ISO format"},
                    "nationality": {"type": "string", "required": False, "description": "Guest's nationality"},
                    "idType": {"type": "string", "required": False, "description": "ID type"},
                    "idNumber": {"type": "string", "required": False, "description": "ID number"},
                    "notes": {"type": "string", "required": False, "description": "Additional notes"}
                }
            }
        },
        "operation": "write"
    },
    {
        "name": "guest_profile_with_reservation",
        "group": "guest_management",
        "function": mcp_tools.guest_profile_with_reservation,
        "description": "Creates a guest profile along with an initial reservation in one operation. Use this when the user wants to create both guest and reservation together.",
        "params": {
            "payload": {
                "type": "object",
                "required": True,
                "description": "Combined guest and reservation data",
                "nested_fields": {
                    "guestName": {"type": "string", "required": True, "description": "Guest's full name (REQUIRED)"},
                    "guestEmail": {"type": "string", "required": True, "description": "Guest's email address (REQUIRED)"},
                    "guestNumber": {"type": "string", "required": True, "description": "Guest's phone number (REQUIRED)"},
                    "checkInDate": {"type": "string", "required": True, "description": "Check-in date in ISO format (YYYY-MM-DD) (REQUIRED)"},
                    "checkOutDate": {"type": "string", "required": True, "description": "Check-out date in ISO format (YYYY-MM-DD) (REQUIRED)"},
                    "roomType": {"type": "string", "required": True, "description": "Room type ObjectId (REQUIRED)"},
                    "totalGuest": {"type": "number", "required": True, "description": "Total number of guests (REQUIRED)"},
                    "mealPlan": {"type": "string", "required": True, "description": "Meal plan code: 'EP', 'CP', 'MAP', or 'AP' (REQUIRED)"},
                    "address": {"type": "object", "required": False, "description": "Guest's address"},
                    "numberOfRooms": {"type": "number", "required": False, "description": "Number of rooms"},
                    "totalAmount": {"type": "number", "required": False, "description": "Total reservation amount"},
                    "notes": {"type": "string", "required": False, "description": "Additional notes"}
                }
            }
        },
        "operation": "write"
    },
    {
        "name": "guest_add_to_reservation",
        "group": "guest_management",
        "function": mcp_tools.guest_add_to_reservation,
        "description": "Adds a guest to an existing reservation. Use this when the user wants to add another guest to a booking.",
        "params": {
            "reservation_id": {"type": "string", "required": True, "description": "The unique identifier of the reservation"},
            "payload": {
                "type": "object",
                "required": True,
                "description": "Guest data to add to the reservation",
                "nested_fields": {
                    "name": {"type": "string", "required": True, "description": "Guest's full name (REQUIRED)"},
                    "email": {"type": "string", "required": True, "description": "Guest's email address (REQUIRED)"},
                    "phone": {"type": "string", "required": True, "description": "Guest's phone number (REQUIRED)"},
                    "dateOfBirth": {"type": "string", "required": False, "description": "Date of birth in ISO format"},
                    "idType": {"type": "string", "required": False, "description": "ID type"},
                    "idNumber": {"type": "string", "required": False, "description": "ID number"}
                }
            }
        },
        "operation": "write"
    },
    {
        "name": "reviews_list",
        "group": "guest_management",
        "function": mcp_tools.reviews_list,
        "description": "Lists reviews with pagination (default: 15 per page). Use this when the user asks to see reviews, reputation, or review list. IMPORTANT: Automatically limits to 15 results per page to avoid token overload.",
        "params": {
            "params": {"type": "object", "required": False, "description": "Query parameters: page (default 1), limit (default 15), source, sentiment, rating, search"}
        },
        "operation": "read"
    },
    {
        "name": "reviews_list_all",
        "group": "guest_management",
        "function": mcp_tools.reviews_list_all,
        "description": "Retrieves all reviews without filtering. Use this when the user asks for all reviews or complete review list.",
        "params": {},
        "operation": "read"
    },
    {
        "name": "review_get",
        "group": "guest_management",
        "function": mcp_tools.review_get,
        "description": "Retrieves a specific review by review ID. Use this when the user asks about a particular review.",
        "params": {
            "review_id": {"type": "string", "required": True, "description": "The unique identifier of the review"}
        },
        "operation": "read"
    },
    {
        "name": "reviews_summarize",
        "group": "guest_management",
        "function": mcp_tools.reviews_summarize,
        "description": "Summarizes multiple reviews. Use this when the user wants a summary of reviews or review analysis.",
        "params": {
            "payload": {
                "type": "object",
                "required": True,
                "description": "Object containing array of review IDs and optional language",
                "nested_fields": {
                    "reviews": {"type": "array", "required": True, "description": "Array of review IDs to summarize (REQUIRED)"},
                    "language": {"type": "string", "required": False, "description": "Language for summary (optional)"}
                }
            }
        },
        "operation": "read"
    },
    {
        "name": "review_create",
        "group": "guest_management",
        "function": mcp_tools.review_create,
        "description": "Creates a new review. Use this when the user wants to add a review or create guest feedback.",
        "params": {
            "payload": {
                "type": "object",
                "required": True,
                "description": "Review data including rating, comment, guest info, etc.",
                "nested_fields": {
                    "name": {"type": "string", "required": True, "description": "Reviewer's name (REQUIRED)"},
                    "rating": {"type": "number", "required": True, "description": "Rating (1-5) (REQUIRED)"},
                    "review": {"type": "string", "required": True, "description": "Review comment/text (REQUIRED)"},
                    "source": {"type": "string", "required": False, "description": "Review source (e.g., 'Google', 'TripAdvisor', 'Direct')"},
                    "guestId": {"type": "string", "required": False, "description": "Guest ObjectId"},
                    "reservationId": {"type": "string", "required": False, "description": "Reservation ObjectId"},
                    "date": {"type": "string", "required": False, "description": "Review date in ISO format (YYYY-MM-DD)"}
                }
            }
        },
        "operation": "write"
    },
    {
        "name": "review_update",
        "group": "guest_management",
        "function": mcp_tools.review_update,
        "description": "Updates an existing review. Use this when the user wants to modify a review.",
        "params": {
            "review_id": {"type": "string", "required": True, "description": "The unique identifier of the review"},
            "payload": {
                "type": "object",
                "required": True,
                "description": "Updated review data",
                "nested_fields": {
                    "name": {"type": "string", "required": False, "description": "Reviewer's name"},
                    "rating": {"type": "number", "required": False, "description": "Rating (1-5)"},
                    "review": {"type": "string", "required": False, "description": "Review comment/text"},
                    "source": {"type": "string", "required": False, "description": "Review source"},
                    "status": {"type": "string", "required": False, "description": "Review status"}
                }
            }
        },
        "operation": "write"
    },
    {
        "name": "review_delete",
        "group": "guest_management",
        "function": mcp_tools.review_delete,
        "description": "Deletes a review. Use this when the user wants to remove a review.",
        "params": {
            "review_id": {"type": "string", "required": True, "description": "The unique identifier of the review to delete"}
        },
        "operation": "write"
    },
    # Distribution & Revenue Tools
    {
        "name": "promotion_list",
        "group": "distribution",
        "function": mcp_tools.promotion_list,
        "description": "Lists all promotions. Use this when the user asks about promotions, discounts, offers, or wants to see all active promotions.",
        "params": {},
        "operation": "read"
    },
    {
        "name": "promotion_create",
        "group": "distribution",
        "function": mcp_tools.promotion_create,
        "description": "Creates a new promotion or discount offer. Use this when the user wants to create a promotion, add a discount, or create an offer.",
        "params": {
            "payload": {
                "type": "object",
                "required": True,
                "description": "Promotion data including name, discount value, discount type (percentage or fixed), coupon code, and end date.",
                "nested_fields": {
                    "name": {"type": "string", "required": True, "description": "Promotion name (e.g., 'Diwali Special', 'New Year Offer') (REQUIRED)"},
                    "couponCode": {"type": "string", "required": True, "description": "Unique coupon code (e.g., 'DIWALI2025', 'NEWYEAR2026') (REQUIRED)"},
                    "lastdate": {"type": "string", "required": True, "description": "End date/validity date in ISO format (YYYY-MM-DD) or ISO datetime. This is when the promotion expires. (REQUIRED)"},
                    "discount": {"type": "number", "required": True, "description": "Discount value. If discountType is 'percentage', this is the percentage (e.g., 50 for 50%). If discountType is 'fixed', this is the fixed amount in currency (e.g., 1000 for ₹1000 off). (REQUIRED)"},
                    "discountType": {"type": "string", "required": True, "description": "Type of discount: 'percentage' (for percentage discount like 20%) or 'fixed' (for fixed amount discount like ₹500 off). Must be exactly 'percentage' or 'fixed'. (REQUIRED)"},
                    "isActive": {"type": "boolean", "required": False, "description": "Whether the promotion is active. Defaults to true if not provided."}
                }
            }
        },
        "operation": "write"
    },
    {
        "name": "promotion_update",
        "group": "distribution",
        "function": mcp_tools.promotion_update,
        "description": "Updates an existing promotion. Use this when the user wants to modify a promotion, change discount, or update offer details.",
        "params": {
            "promo_id": {"type": "string", "required": True, "description": "The unique identifier (ObjectId) of the promotion to update"},
            "payload": {
                "type": "object",
                "required": True,
                "description": "Updated promotion data. Only include fields that need to be updated.",
                "nested_fields": {
                    "name": {"type": "string", "required": False, "description": "Promotion name"},
                    "couponCode": {"type": "string", "required": False, "description": "Coupon code"},
                    "lastdate": {"type": "string", "required": False, "description": "End date/validity date in ISO format (YYYY-MM-DD) or ISO datetime"},
                    "discount": {"type": "number", "required": False, "description": "Discount value. If discountType is 'percentage', this is the percentage (e.g., 50 for 50%). If discountType is 'fixed', this is the fixed amount in currency (e.g., 1000 for ₹1000 off)."},
                    "discountType": {"type": "string", "required": False, "description": "Type of discount: 'percentage' (for percentage discount) or 'fixed' (for fixed amount discount). Must be exactly 'percentage' or 'fixed'."},
                    "isActive": {"type": "boolean", "required": False, "description": "Whether the promotion is active"}
                }
            }
        },
        "operation": "write"
    },
    {
        "name": "promotion_delete",
        "group": "distribution",
        "function": mcp_tools.promotion_delete,
        "description": "Deletes a promotion. Use this when the user wants to remove a promotion or cancel an offer.",
        "params": {
            "promo_id": {"type": "string", "required": True, "description": "The unique identifier of the promotion to delete"}
        },
        "operation": "write"
    },
    {
        "name": "rates_get",
        "group": "distribution",
        "function": mcp_tools.rates_get,
        "description": "Retrieves rate information with optional filtering. Use this when the user asks about rates, pricing, or wants to see rate plans.",
        "params": {
            "params": {"type": "object", "required": False, "description": "Optional query parameters for filtering rates"}
        },
        "operation": "read"
    },
    {
        "name": "rates_set",
        "group": "distribution",
        "function": mcp_tools.rates_set,
        "description": "Sets or updates room rates. Use this when the user wants to set rates, update pricing, or change room rates.",
        "params": {
            "payload": {
                "type": "object",
                "required": True,
                "description": "Rate data including room type, dates, and pricing. For perPerson model: adultPrice and childPrice are required. For perRoom model: baseRate is required.",
                "nested_fields": {
                    "roomTypeId": {"type": "string", "required": True, "description": "Room type ObjectId (REQUIRED)"},
                    "dates": {"type": "array", "required": True, "description": "Array of date strings in YYYY-MM-DD format (REQUIRED). Example: ['2025-12-08'] for single date or ['2025-12-08', '2025-12-09'] for multiple dates."},
                    "priceModel": {"type": "string", "required": True, "description": "Price model: 'perPerson' or 'perRoom' (REQUIRED)"},
                    "adultPrice": {"type": "number", "required": False, "description": "Adult rate (REQUIRED if priceModel is 'perPerson')"},
                    "childPrice": {"type": "number", "required": False, "description": "Child rate (optional, defaults to 0 if priceModel is 'perPerson')"},
                    "baseRate": {"type": "number", "required": False, "description": "Base room rate (REQUIRED if priceModel is 'perRoom')"},
                    "extraGuestRate": {"type": "number", "required": False, "description": "Extra guest rate (optional, defaults to 0 if priceModel is 'perRoom')"}
                }
            }
        },
        "operation": "write"
    },
    {
        "name": "inventory_monthly",
        "group": "distribution",
        "function": mcp_tools.inventory_monthly,
        "description": "Gets monthly inventory report. Use this when the user asks for monthly inventory, availability report, or monthly availability.",
        "params": {
            "params": {"type": "object", "required": False, "description": "Optional query parameters like month, year, etc."}
        },
        "operation": "read"
    },
    {
        "name": "inventory_availability",
        "group": "distribution",
        "function": mcp_tools.inventory_availability,
        "description": "Gets inventory availability information. Use this when the user asks about inventory availability or room availability.",
        "params": {
            "params": {"type": "object", "required": False, "description": "Optional query parameters for filtering availability"}
        },
        "operation": "read"
    },
    {
        "name": "inventory_room_types_availability",
        "group": "distribution",
        "function": mcp_tools.inventory_room_types_availability,
        "description": "Gets availability by room types. Use this when the user asks about availability for specific room types.",
        "params": {
            "params": {"type": "object", "required": False, "description": "Optional query parameters for filtering by room type"}
        },
        "operation": "read"
    },
    {
        "name": "inventory_blocks_list",
        "group": "distribution",
        "function": mcp_tools.inventory_blocks_list,
        "description": "Lists all inventory blocks. Use this when the user asks about blocked inventory or inventory blocks.",
        "params": {},
        "operation": "read"
    },
    {
        "name": "inventory_block",
        "group": "distribution",
        "function": mcp_tools.inventory_block,
        "description": "Blocks inventory for specific dates and room types. Use this when the user wants to block rooms, prevent bookings, or reserve inventory.",
        "params": {
            "payload": {
                "type": "object",
                "required": True,
                "description": "Block data including dates, room types, reason, etc.",
                "nested_fields": {
                    "startDate": {"type": "string", "required": True, "description": "Start date in ISO format (YYYY-MM-DD) (REQUIRED)"},
                    "endDate": {"type": "string", "required": True, "description": "End date in ISO format (YYYY-MM-DD) (REQUIRED)"},
                    "roomTypeIds": {"type": "array", "required": True, "description": "Array of room type ObjectIds to block (REQUIRED)"},
                    "reason": {"type": "string", "required": True, "description": "Reason for blocking (REQUIRED)"},
                    "numberOfRooms": {"type": "number", "required": False, "description": "Number of rooms to block per room type"}
                }
            }
        },
        "operation": "write"
    },
    {
        "name": "inventory_unblock",
        "group": "distribution",
        "function": mcp_tools.inventory_unblock,
        "description": "Unblocks previously blocked inventory. Use this when the user wants to unblock rooms or release blocked inventory.",
        "params": {
            "payload": {
                "type": "object",
                "required": True,
                "description": "Unblock data including block ID or dates and room types",
                "nested_fields": {
                    "blockId": {"type": "string", "required": False, "description": "Block ObjectId (if unblocking by ID)"},
                    "startDate": {"type": "string", "required": False, "description": "Start date in ISO format (if unblocking by date range)"},
                    "endDate": {"type": "string", "required": False, "description": "End date in ISO format (if unblocking by date range)"},
                    "roomTypeIds": {"type": "array", "required": False, "description": "Array of room type ObjectIds (if unblocking by date range)"}
                }
            }
        },
        "operation": "write"
    },
    # Foundation & Rooms Tools
    {
        "name": "rooms_list",
        "group": "foundation",
        "function": mcp_tools.rooms_list,
        "description": "Lists all physical rooms (no pagination). Use this when the user asks to see all rooms, room list, or room inventory. Note: May return many rooms for large hotels.",
        "params": {
            "params": {"type": "object", "required": False, "description": "Query parameters if available"}
        },
        "operation": "read"
    },
    {
        "name": "room_get",
        "group": "foundation",
        "function": mcp_tools.room_get,
        "description": "Retrieves details about a specific room by room ID. Use this when the user asks about a particular room.",
        "params": {
            "room_id": {"type": "string", "required": True, "description": "The unique identifier of the room"}
        },
        "operation": "read"
    },
    {
        "name": "room_types_list",
        "group": "foundation",
        "function": mcp_tools.room_types_list,
        "description": "Lists all room types. Use this when the user asks about room types, room categories, or available room types.",
        "params": {},
        "operation": "read"
    },
    {
        "name": "room_type_get",
        "group": "foundation",
        "function": mcp_tools.room_type_get,
        "description": "Retrieves details about a specific room type by room type ID. Use this when the user asks about a particular room type.",
        "params": {
            "room_type_id": {"type": "string", "required": True, "description": "The unique identifier of the room type"}
        },
        "operation": "read"
    },
    {
        "name": "room_update_status_by_number",
        "group": "foundation",
        "function": mcp_tools.room_update_status_by_number,
        "description": "Updates room status by room number/name. Use this when the user wants to block a room, unblock a room, set room to maintenance, clean, dirty, or change room status. Common statuses: 'available', 'occupied', 'maintenance', 'out-of-order', 'dirty', 'clean', 'blocked'.",
        "params": {
            "payload": {
                "type": "object",
                "required": True,
                "description": "Room status update data",
                "nested_fields": {
                    "roomNumber": {"type": "string", "required": True, "description": "Room number or room name (e.g., '101', 'Room 101') (REQUIRED)"},
                    "status": {"type": "string", "required": True, "description": "New room status. Common values: 'available', 'occupied', 'maintenance', 'out-of-order', 'dirty', 'clean', 'blocked' (REQUIRED)"}
                }
            }
        },
        "operation": "write"
    },
    {
        "name": "room_update_status",
        "group": "foundation",
        "function": mcp_tools.room_update_status,
        "description": "Updates room status by room ID. Use this when the user wants to change room status and you have the room ID. Common statuses: 'available', 'occupied', 'maintenance', 'out-of-order', 'dirty', 'clean', 'blocked'.",
        "params": {
            "room_id": {"type": "string", "required": True, "description": "The unique identifier of the room (ObjectId)"},
            "payload": {
                "type": "object",
                "required": True,
                "description": "Room status update data",
                "nested_fields": {
                    "status": {"type": "string", "required": True, "description": "New room status. Common values: 'available', 'occupied', 'maintenance', 'out-of-order', 'dirty', 'clean', 'blocked' (REQUIRED)"}
                }
            }
        },
        "operation": "write"
    },
    # Communication Tools
    {
        "name": "conversations_list",
        "group": "communication",
        "function": mcp_tools.conversations_list,
        "description": "Lists conversations with pagination (default: 20 per page). Use this when the user asks to see conversations, messages, or communication list. IMPORTANT: Automatically limits to 20 results per page to avoid token overload.",
        "params": {
            "params": {"type": "object", "required": False, "description": "Query parameters: page (default 1), limit (default 20), status, search, assignedTo"}
        },
        "operation": "read"
    },
    {
        "name": "conversation_get",
        "group": "communication",
        "function": mcp_tools.conversation_get,
        "description": "Retrieves a specific conversation by conversation ID. Use this when the user asks about a particular conversation.",
        "params": {
            "conversation_id": {"type": "string", "required": True, "description": "The unique identifier of the conversation"}
        },
        "operation": "read"
    },
    {
        "name": "conversation_messages",
        "group": "communication",
        "function": mcp_tools.conversation_messages,
        "description": "Gets all messages in a conversation. Use this when the user asks to see messages in a conversation or message history.",
        "params": {
            "conversation_id": {"type": "string", "required": True, "description": "The unique identifier of the conversation"}
        },
        "operation": "read"
    },
    {
        "name": "conversation_add_message",
        "group": "communication",
        "function": mcp_tools.conversation_add_message,
        "description": "Adds a message to a conversation. Use this when the user wants to send a message or reply to a conversation.",
        "params": {
            "conversation_id": {"type": "string", "required": True, "description": "The unique identifier of the conversation"},
            "payload": {
                "type": "object",
                "required": True,
                "description": "Message data including content, sender, etc.",
                "nested_fields": {
                    "content": {"type": "string", "required": True, "description": "Message content/text (REQUIRED)"},
                    "sender": {"type": "string", "required": True, "description": "Sender name or ID (REQUIRED)"},
                    "senderType": {"type": "string", "required": False, "description": "Sender type (e.g., 'staff', 'guest', 'system')"},
                    "attachments": {"type": "array", "required": False, "description": "Array of attachment URLs or objects"}
                }
            }
        },
        "operation": "write"
    },
    {
        "name": "conversation_mark_read",
        "group": "communication",
        "function": mcp_tools.conversation_mark_read,
        "description": "Marks messages in a conversation as read. Use this when the user wants to mark messages as read.",
        "params": {
            "conversation_id": {"type": "string", "required": True, "description": "The unique identifier of the conversation"},
            "payload": {
                "type": "object",
                "required": True,
                "description": "Data specifying which messages to mark as read",
                "nested_fields": {
                    "messageIds": {"type": "array", "required": False, "description": "Array of message IDs to mark as read (if specific messages)"},
                    "markAll": {"type": "boolean", "required": False, "description": "Mark all messages as read (if true)"}
                }
            }
        },
        "operation": "write"
    },
    {
        "name": "campaign_create",
        "group": "communication",
        "function": mcp_tools.campaign_create,
        "description": "Creates a new marketing campaign. Use this when the user wants to create a campaign, marketing message, or promotional campaign.",
        "params": {
            "payload": {
                "type": "object",
                "required": True,
                "description": "Campaign data including name, message, target audience, etc.",
                "nested_fields": {
                    "name": {"type": "string", "required": True, "description": "Campaign name (REQUIRED)"},
                    "message": {"type": "string", "required": True, "description": "Campaign message/content (REQUIRED)"},
                    "targetAudience": {"type": "string", "required": True, "description": "Target audience (e.g., 'all', 'guests', 'reservations') (REQUIRED)"},
                    "sendDate": {"type": "string", "required": False, "description": "Send date in ISO format (YYYY-MM-DD)"},
                    "channel": {"type": "string", "required": False, "description": "Channel (e.g., 'email', 'sms', 'whatsapp')"},
                    "guestIds": {"type": "array", "required": False, "description": "Array of guest ObjectIds (if targeting specific guests)"}
                }
            }
        },
        "operation": "write"
    },
    # Settings Tools
    {
        "name": "settings_get_ai",
        "group": "settings",
        "function": mcp_tools.settings_get_ai,
        "description": "Retrieves AI settings configuration. Use this when the user asks about AI settings or configuration.",
        "params": {},
        "operation": "read"
    },
    {
        "name": "settings_update_ai",
        "group": "settings",
        "function": mcp_tools.settings_update_ai,
        "description": "Updates AI settings configuration. Use this when the user wants to update AI settings or change configuration.",
        "params": {
            "payload": {
                "type": "object",
                "required": True,
                "description": "Updated AI settings data",
                "nested_fields": {
                    "model": {"type": "string", "required": False, "description": "AI model name"},
                    "temperature": {"type": "number", "required": False, "description": "Temperature setting (0-1)"},
                    "maxTokens": {"type": "number", "required": False, "description": "Maximum tokens"},
                    "systemPrompt": {"type": "string", "required": False, "description": "System prompt"},
                    "enabled": {"type": "boolean", "required": False, "description": "Whether AI is enabled"},
                    "features": {"type": "object", "required": False, "description": "AI features configuration"}
                }
            }
        },
        "operation": "write"
    }
]


INTENT_SYSTEM_PROMPT = (
    "You are an expert Hotel AI Assistant. Your job is to classify user intent and route to the correct tool group.\n\n"
    "IMPORTANT LANGUAGE HANDLING:\n"
    "- You understand ANY language the user speaks\n"
    "- You MUST respond ONLY in English with valid JSON\n"
    "- Do NOT translate the user's message, just understand it and classify the intent\n\n"
    "IMPORTANT: You must Consider the CONTEXT of previous messages. If the user asks a follow-up question "
    "(e.g., 'explain that', 'why is that', 'in hindi', 'details'), maintain the context of the previous conversation. "
    "Only classify as a NEW query if the user explicitly changes the topic (e.g., asking about 'bills' after asking about 'arrivals').\n\n"
    "MULTI-TASK DETECTION:\n"
    "- If the user requests MULTIPLE DIFFERENT tasks (e.g., 'create a reservation and create a coupon'), set is_multi_task=true\n"
    "- If the user requests the SAME task for MULTIPLE ITEMS (e.g., 'put rooms 101, 102, 103 in maintenance'), set is_multi_task=true\n"
    "- If is_multi_task=true, you can specify multiple tool_groups as an array, or a single tool_group if all tasks are in the same group\n\n"
    "TOOL GROUPS (Choose wisely):\n"
    "- frontoffice: RESERVATIONS & OPERATIONS. Use for checking availability, creating/finding bookings, check-in/out, and daily arrival/departure lists.\n"
    "- billing_finance: MONEY & BILLS. Use for guest folios, adding charges, payments, checkout billing, and invoices.\n"
    "- guest_management: GUESTS & REVIEWS. Use for guest profiles, history, preferences, and managing reviews/reputation.\n"
    "- distribution: REVENUE & INVENTORY. Use for setting rates, creating promotions/coupons, and blocking inventory/rooms.\n"
    "- foundation: ROOMS & MAINTENANCE. Use for physical room status (clean/dirty/maintenance), room types, and housekeeping tasks.\n"
    "- communication: MESSAGING. Use for sending messages to guests or managing campaigns.\n"
    "- settings: CONFIG. Use for system settings and AI configuration.\n\n"
    "Return STRICT JSON with keys:\n"
    "- _thought: string (Briefly explain your reasoning. What is the user really asking? What context is relevant?)\n"
    "- intent: 'small_talk' | 'info_read' | 'task_write'\n"
    "- is_multi_task: boolean (true if user wants multiple tasks, false otherwise)\n"
    "- tool_group: string | array | null (one tool group, array of tool groups for multi-task, or null for small_talk)\n"
    "- tool: string | null (specific tool name if obvious, otherwise null)\n"
    "- params: object | null (initial params if extractable, or null)\n\n"
    "INTENT RULES:\n"
    "- Use 'small_talk' for: General definitions (e.g., 'What is ADR?'), explanations, greetings, or questions that don't need live database access.\n"
    "- Use 'info_read' for: Fetching specific hotel records (e.g., 'Get bookings', 'Who is checking in?').\n"
    "- Use 'task_write' for: Creating/Updating/Deleting records.\n\n"
    "Only specify tool_group and tool for 'info_read' or 'task_write'. Do not add extra text. "
    "Return ONLY valid JSON in English, no markdown, no explanations."
)

SUMMARY_SYSTEM_PROMPT = (
    "You are an expert Hotel Operations Assistant. Your goal is to provide world-class, high-level executive summaries "
    "and helpful responses to hotel staff.\n\n"
    "GUIDELINES:\n"
    "1. Be High-Level & Insightful: Don't just list data. Synthesize it. Instead of 'There are 0 arrivals', say 'There are no arrivals scheduled for today.'\n"
    "2. Be Proactive: If a task failed or data is missing, suggest the next best step.\n"
    "3. Tone: Professional, efficient, yet friendly. Match the user's language and formality.\n"
    "4. Formatting: Use bullet points for lists. Use bolding for key figures (e.g., **5 arrivals**, **₹50,000**).\n"
    "5. Privacy: Never expose raw internal IDs unless explicitly asked.\n"
    "6. Conciseness: Get to the point. Busy hotel staff need answers fast.\n\n"
    f"Format strictly according to the user's requested style ({settings.summary_style})."
)


def _safe_json_loads(text: str) -> dict:
    """Safely parse JSON from LLM output, handling markdown code blocks and extra text."""
    if not text or not text.strip():
        # Return a default empty dict instead of raising error - let caller handle it
        return {}
    
    text = text.strip()
    
    # Try plain json first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    
    # Try to extract JSON from markdown code blocks (```json ... ```)
    json_block_pattern = r'```(?:json)?\s*(\{.*?\})\s*```'
    matches = re.findall(json_block_pattern, text, re.DOTALL)
    if matches:
        for match in matches:
            try:
                return json.loads(match.strip())
            except json.JSONDecodeError:
                continue
    
    # Try to extract first balanced JSON object
    brace = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if start < 0:
                start = i
            brace += 1
        elif ch == "}":
            brace -= 1
            if brace == 0 and start >= 0:
                segment = text[start : i + 1]
                try:
                    return json.loads(segment)
                except json.JSONDecodeError:
                    start = -1
                    continue
    
    # Last resort: try to find any JSON-like structure
    json_pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
    matches = re.findall(json_pattern, text, re.DOTALL)
    for match in matches:
        try:
            return json.loads(match)
        except json.JSONDecodeError:
            continue
    
    # Log the problematic text for debugging
    print(f"DEBUG: Failed to parse JSON. LLM output: {text[:500]}...")
    raise ValueError(f"Unable to parse JSON from LLM output. Response: {text[:200]}...")


def _validate_classification(obj: dict) -> dict:
    if not isinstance(obj, dict):
        raise ValueError("Classification is not an object")
    intent = obj.get("intent")
    if intent not in {"small_talk", "info_read", "task_write"}:
        raise ValueError(f"Invalid intent: {intent}")
    # tool_group can be None, string, or array (for multi-task)
    tool_group = obj.get("tool_group")
    if tool_group is not None:
        if not isinstance(tool_group, (str, list)):
            raise ValueError("tool_group must be string, array, or null")
        if isinstance(tool_group, list):
            # Validate array elements are strings
            for tg in tool_group:
                if not isinstance(tg, str):
                    raise ValueError("tool_group array elements must be strings")
    # tool can be None or string
    tool = obj.get("tool")
    if tool is not None and not isinstance(tool, str):
        raise ValueError("tool must be string or null")
    # params can be None or dict
    params = obj.get("params")
    if params is not None and not isinstance(params, dict):
        raise ValueError("params must be object or null")
    # is_multi_task is optional boolean
    is_multi_task = obj.get("is_multi_task", False)
    if not isinstance(is_multi_task, bool):
        obj["is_multi_task"] = False
    return obj


def _get_safe_messages(messages: List[Message], limit: int = 10) -> List[Dict[str, str]]:
    """
    Get safe messages for LLM API that requires User/Assistant alternation starting with User.
    Removes system messages from history.
    Ensures the first message is from 'user'.
    """
    # 1. Get candidates (excluding system)
    candidates = [m for m in messages if m.role != "system"]
    
    # 2. Slice to limit
    sliced = candidates[-limit:] if limit > 0 else candidates
    
    if not sliced:
        return [{"role": "user", "content": "Please proceed."}]
        
    # 3. Check if first message is user
    if sliced[0].role == "user":
        return [m.model_dump() for m in sliced]
        
    # 4. If not, try to find a preceding user message from the FULL list
    # Find index of first sliced message in candidates
    first_msg = sliced[0]
    try:
        idx = candidates.index(first_msg)
        # Search backwards from idx-1
        for i in range(idx - 1, -1, -1):
            if candidates[i].role == "user":
                return [candidates[i].model_dump()] + [m.model_dump() for m in sliced]
    except ValueError:
        pass
        
    # 5. If no preceding user message found, prepend a dummy one
    return [{"role": "user", "content": "Please proceed."}] + [m.model_dump() for m in sliced]



def _get_tools_by_group(group: str, operation: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get tools filtered by group and optionally by operation (read/write)."""
    filtered = [t for t in TOOLS_METADATA if t["group"] == group]
    if operation:
        filtered = [t for t in filtered if t["operation"] == operation]
    return filtered


def _get_tool_metadata(tool_name: str) -> Optional[Dict[str, Any]]:
    """Get metadata for a specific tool by name."""
    for tool in TOOLS_METADATA:
        if tool["name"] == tool_name:
            return tool
    return None


def _filter_params(fn: Callable, params: Dict[str, Any]) -> Dict[str, Any]:
    """Filter params to only include those accepted by the function signature."""
    sig = inspect.signature(fn)
    valid_params = {}
    for param_name, param_obj in sig.parameters.items():
        if param_name in params:
            valid_params[param_name] = params[param_name]
    return valid_params


def _normalize_tool_result(result: Any) -> Dict[str, Any]:
    """Normalize tool result to dict format for event emission."""
    if isinstance(result, dict):
        return result
    if isinstance(result, list):
        return {"items": result, "count": len(result)}
    # For other types (str, int, etc.), wrap in dict
    return {"value": result}


def _resolve_tool(tool_name: str) -> Callable[..., object]:
    """Resolve tool name to function using metadata."""
    tool_meta = _get_tool_metadata(tool_name)
    if not tool_meta:
        raise ValueError(f"Unknown tool: {tool_name}")
    return tool_meta["function"]


# ID Enrichment Functions - Convert IDs to human-readable names/context
async def _enrich_room_type_id(room_type_id: str, token: Optional[str] = None) -> Optional[str]:
    """Fetch room type name by ID."""
    try:
        room_type = await mcp_tools.room_type_get(room_type_id, token=token)
        if room_type and isinstance(room_type, dict):
            name = room_type.get("name", "")
            base_rate = room_type.get("baseRate", 0)
            if name:
                return f"{name} (₹{base_rate}/night)" if base_rate else name
        return None
    except Exception as e:
        print(f"Error enriching room type ID {room_type_id}: {e}")
        return None


async def _enrich_room_id(room_id: str, token: Optional[str] = None) -> Optional[str]:
    """Fetch room number by ID."""
    try:
        room = await mcp_tools.room_get(room_id, token=token)
        if room and isinstance(room, dict):
            room_number = room.get("roomNumber", "")
            room_type_id = room.get("roomType", "")
            if room_number:
                # Also enrich room type if available
                room_type_name = None
                if room_type_id:
                    room_type_name = await _enrich_room_type_id(room_type_id, token=token)
                if room_type_name:
                    return f"{room_number} ({room_type_name})"
                return room_number
        return None
    except Exception as e:
        print(f"Error enriching room ID {room_id}: {e}")
        return None


async def _enrich_guest_id(guest_id: str, token: Optional[str] = None) -> Optional[str]:
    """Fetch guest name by ID."""
    try:
        # Note: We might need to add a guest_get function if it doesn't exist
        # For now, try to get from guests list (less efficient but works)
        guests = await mcp_tools.guests_list(token=token)
        if isinstance(guests, list):
            for guest in guests:
                if isinstance(guest, dict) and guest.get("_id") == guest_id:
                    name = guest.get("name", "")
                    email = guest.get("email", "")
                    if name:
                        return f"{name} ({email})" if email else name
        return None
    except Exception as e:
        print(f"Error enriching guest ID {guest_id}: {e}")
        return None


async def _enrich_reservation_id(reservation_id: str, token: Optional[str] = None) -> Optional[str]:
    """Fetch reservation details by ID."""
    try:
        reservation = await mcp_tools.reservation_get(reservation_id, token=token)
        if reservation and isinstance(reservation, dict):
            guest_name = reservation.get("guestName", "")
            confirmation = reservation.get("confirmationNumber", "")
            check_in = reservation.get("checkInDate", "")
            if guest_name:
                details = f"{guest_name}"
                if confirmation:
                    details += f" (Confirmation: {confirmation})"
                if check_in:
                    details += f" - Check-in: {check_in}"
                return details
        return None
    except Exception as e:
        print(f"Error enriching reservation ID {reservation_id}: {e}")
        return None


async def _enrich_folio_id(folio_id: str, token: Optional[str] = None) -> Optional[str]:
    """Fetch folio details by ID."""
    try:
        folio = await mcp_tools.folio_get(folio_id, token=token)
        if folio and isinstance(folio, dict):
            folio_number = folio.get("folioNumber", "")
            guest_id = folio.get("guestId", "")
            total_amount = folio.get("totalAmount", 0)
            if folio_number:
                details = f"Folio #{folio_number}"
                if guest_id:
                    guest_name = await _enrich_guest_id(guest_id, token=token)
                    if guest_name:
                        details += f" - {guest_name}"
                if total_amount:
                    details += f" (₹{total_amount})"
                return details
        return None
    except Exception as e:
        print(f"Error enriching folio ID {folio_id}: {e}")
        return None


async def _enrich_promotion_id(promo_id: str, token: Optional[str] = None) -> Optional[str]:
    """Fetch promotion name by ID."""
    try:
        promotions = await mcp_tools.promotion_list(token=token)
        if isinstance(promotions, list):
            for promo in promotions:
                if isinstance(promo, dict) and promo.get("_id") == promo_id:
                    name = promo.get("name", "")
                    coupon_code = promo.get("couponCode", "")
                    discount = promo.get("discountPercentage", 0)
                    if name:
                        details = f"{name}"
                        if coupon_code:
                            details += f" ({coupon_code})"
                        if discount:
                            details += f" - {discount}% off"
                        return details
        return None
    except Exception as e:
        print(f"Error enriching promotion ID {promo_id}: {e}")
        return None


async def _enrich_id_context(param_name: str, param_value: Any, token: Optional[str] = None) -> Optional[str]:
    """
    Enrich an ID parameter with its human-readable context.
    Returns enriched description or None if enrichment fails.
    """
    if not isinstance(param_value, str) or not param_value:
        return None
    
    # Check if it looks like a MongoDB ObjectId (24 hex characters)
    if not re.match(r'^[0-9a-fA-F]{24}$', param_value):
        return None
    
    # Map parameter names to enrichment functions
    enrichment_map = {
        "roomType": _enrich_room_type_id,
        "roomTypeId": _enrich_room_type_id,
        "room_id": _enrich_room_id,
        "roomId": _enrich_room_id,
        "roomNumbers": None,  # Array - handled separately
        "guestId": _enrich_guest_id,
        "guest_id": _enrich_guest_id,
        "reservationId": _enrich_reservation_id,
        "reservation_id": _enrich_reservation_id,
        "folioId": _enrich_folio_id,
        "folio_id": _enrich_folio_id,
        "promoId": _enrich_promotion_id,
        "promo_id": _enrich_promotion_id,
    }
    
    enrichment_func = enrichment_map.get(param_name)
    if enrichment_func:
        return await enrichment_func(param_value, token=token)
    
    return None


async def _enrich_missing_params(
    missing_params: Dict[str, Any],
    token: Optional[str] = None
) -> Dict[str, Any]:
    """
    Enrich missing parameters with human-readable context for IDs.
    Returns enriched missing_params with context added.
    """
    enriched = {}
    
    for param_name, param_desc in missing_params.items():
        enriched[param_name] = param_desc
        
        # Check if this is a nested structure (e.g., payload.roomType)
        if isinstance(param_desc, dict):
            # Recursively enrich nested params
            enriched[param_name] = await _enrich_missing_params(param_desc, token=token)
        elif isinstance(param_desc, str):
            # Check if description mentions an ID that we can enrich
            # Look for common ID patterns in the description
            if "ObjectId" in param_desc or "ID" in param_desc or "id" in param_desc:
                # Try to enrich if we have a value (but we don't in missing_params)
                # Instead, add helpful context about what IDs are available
                if "roomType" in param_name.lower() or "roomtype" in param_name.lower():
                    enriched[param_name] = f"{param_desc}. You can get available room types by asking 'show me room types' or 'list room types'."
                elif "room" in param_name.lower() and "number" not in param_name.lower():
                    enriched[param_name] = f"{param_desc}. You can get available rooms by asking 'show me rooms' or 'list rooms'."
                elif "guest" in param_name.lower():
                    enriched[param_name] = f"{param_desc}. You can get available guests by asking 'show me guests' or 'list guests'."
    
    return enriched


async def _convert_names_to_ids_in_params(extracted: Dict[str, Any], token: Optional[str] = None) -> Dict[str, Any]:
    """
    Post-process extracted parameters to convert names to IDs where applicable.
    Recursively processes nested structures.
    """
    if not isinstance(extracted, dict):
        return extracted
    
    tool_name = extracted.get("tool")
    params = extracted.get("params", {})
    
    if not tool_name or not params:
        return extracted
    
    # Get tool metadata to understand parameter structure
    tool_meta = _get_tool_metadata(tool_name)
    if not tool_meta:
        return extracted
    
    # Helper function to convert IDs in a dictionary
    async def convert_dict(params_dict: Dict[str, Any], params_spec: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
        converted = {}
        for key, value in params_dict.items():
            full_key = f"{prefix}.{key}" if prefix else key
            param_spec = params_spec.get(key, {})
            
            if isinstance(value, dict):
                # Handle nested structures
                nested_spec = param_spec.get("nested_fields", {}) if isinstance(param_spec, dict) else {}
                converted[key] = await convert_dict(value, nested_spec, full_key)
            elif isinstance(value, list):
                # Handle arrays (e.g., roomNumbers)
                converted_list = []
                for item in value:
                    if isinstance(item, str) and not re.match(r'^[0-9a-fA-F]{24}$', item):
                        # It's not an ID, try to resolve it
                        resolved_id = await _resolve_name_to_id(key, item, token=token)
                        converted_list.append(resolved_id if resolved_id else item)
                    else:
                        converted_list.append(item)
                converted[key] = converted_list
            elif isinstance(value, str) and not re.match(r'^[0-9a-fA-F]{24}$', value):
                # It's a string that's not an ID - check if it should be an ID
                key_lower = key.lower()
                if any(id_term in key_lower for id_term in ["roomtype", "room_id", "roomid", "guestid", "guest_id", "reservationid", "folioid", "promoid"]):
                    # Try to resolve name to ID
                    resolved_id = await _resolve_name_to_id(key, value, token=token)
                    if resolved_id:
                        converted[key] = resolved_id
                    else:
                        converted[key] = value
                else:
                    converted[key] = value
            else:
                converted[key] = value
        
        return converted
    
    # Convert params
    params_spec = tool_meta.get("params", {})
    converted_params = await convert_dict(params, params_spec)
    
    # Update extracted dict
    extracted["params"] = converted_params
    return extracted


async def _resolve_name_to_id(param_name: str, user_input: str, token: Optional[str] = None) -> Optional[str]:
    """
    Resolve a user-provided name to its corresponding ID.
    Returns the ID if found, None otherwise.
    """
    param_lower = param_name.lower()
    user_input_lower = user_input.lower().strip()
    
    try:
        if "roomtype" in param_lower or "room_type" in param_lower:
            room_types = await mcp_tools.room_types_list(token=token)
            if isinstance(room_types, list):
                for rt in room_types:
                    if isinstance(rt, dict):
                        name = rt.get("name", "").lower()
                        rt_id = rt.get("_id", "")
                        # Check if user input matches name (partial or full)
                        if name and rt_id and (user_input_lower in name or name in user_input_lower):
                            return rt_id
        
        elif "room" in param_lower and ("number" in param_lower or "name" in param_lower):
            # For room number/name, return the input as-is (the API accepts room number directly)
            # But we can validate it exists
            rooms = await mcp_tools.rooms_list(token=token)
            if isinstance(rooms, list):
                for room in rooms:
                    if isinstance(room, dict):
                        room_number = room.get("roomNumber", "").lower()
                        # Check if user input matches room number (exact or partial)
                        if room_number and (user_input_lower == room_number or user_input_lower in room_number or room_number in user_input_lower):
                            # Return the actual room number from the system (normalized)
                            return room.get("roomNumber", user_input)
                # If not found, return input as-is (might be a new room or different format)
                return user_input
        elif "room" in param_lower and "id" in param_lower and "number" not in param_lower:
            rooms = await mcp_tools.rooms_list(token=token)
            if isinstance(rooms, list):
                for room in rooms:
                    if isinstance(room, dict):
                        room_number = room.get("roomNumber", "").lower()
                        room_id = room.get("_id", "")
                        # Check if user input matches room number (convert to ID)
                        if room_number and room_id and (user_input_lower == room_number or user_input_lower in room_number or room_number in user_input_lower):
                            return room_id
        
        elif "guest" in param_lower and "id" in param_lower:
            guests = await mcp_tools.guests_list(token=token)
            if isinstance(guests, list):
                for guest in guests:
                    if isinstance(guest, dict):
                        name = guest.get("name", "").lower()
                        email = guest.get("email", "").lower()
                        guest_id = guest.get("_id", "")
                        # Check if user input matches name or email
                        if guest_id and (user_input_lower in name or name in user_input_lower or user_input_lower in email):
                            return guest_id
        
        elif "promotion" in param_lower or "promo" in param_lower:
            promotions = await mcp_tools.promotion_list(token=token)
            if isinstance(promotions, list):
                for promo in promotions:
                    if isinstance(promo, dict):
                        name = promo.get("name", "").lower()
                        coupon = promo.get("couponCode", "").lower()
                        promo_id = promo.get("_id", "")
                        # Check if user input matches name or coupon code
                        if promo_id and (user_input_lower in name or name in user_input_lower or user_input_lower in coupon):
                            return promo_id
    except Exception as e:
        print(f"Error resolving name to ID for {param_name}: {e}")
    
    return None


async def _get_available_options_for_param(param_name: str, token: Optional[str] = None) -> Optional[str]:
    """
    Get available options for a parameter that requires an ID.
    Returns a formatted string with available options or None.
    """
    param_lower = param_name.lower()
    
    try:
        if "roomtype" in param_lower or "room_type" in param_lower:
            room_types = await mcp_tools.room_types_list(token=token)
            if isinstance(room_types, list) and room_types:
                options = []
                for rt in room_types[:10]:  # Limit to 10 for brevity
                    if isinstance(rt, dict):
                        name = rt.get("name", "")
                        rt_id = rt.get("_id", "")
                        base_rate = rt.get("baseRate", 0)
                        if name and rt_id:
                            options.append(f"- {name} (ID: {rt_id}, Rate: ₹{base_rate}/night)")
                if options:
                    return "Available Room Types:\n" + "\n".join(options)
        
        elif "room" in param_lower and ("number" in param_lower or "name" in param_lower):
            rooms = await mcp_tools.rooms_list(token=token)
            if isinstance(rooms, list) and rooms:
                options = []
                for room in rooms[:20]:  # Limit to 20 for room numbers
                    if isinstance(room, dict):
                        room_number = room.get("roomNumber", "")
                        room_status = room.get("status", "")
                        if room_number:
                            status_str = f" ({room_status})" if room_status else ""
                            options.append(f"- Room {room_number}{status_str}")
                if options:
                    return "Available Rooms:\n" + "\n".join(options)
        elif "room" in param_lower and "id" in param_lower and "number" not in param_lower:
            rooms = await mcp_tools.rooms_list(token=token)
            if isinstance(rooms, list) and rooms:
                options = []
                for room in rooms[:10]:  # Limit to 10
                    if isinstance(room, dict):
                        room_number = room.get("roomNumber", "")
                        room_id = room.get("_id", "")
                        if room_number and room_id:
                            options.append(f"- Room {room_number} (ID: {room_id})")
                if options:
                    return "Available Rooms:\n" + "\n".join(options)
        
        elif "guest" in param_lower and "id" in param_lower:
            guests = await mcp_tools.guests_list(token=token)
            if isinstance(guests, list) and guests:
                options = []
                for guest in guests[:10]:  # Limit to 10
                    if isinstance(guest, dict):
                        name = guest.get("name", "")
                        guest_id = guest.get("_id", "")
                        email = guest.get("email", "")
                        if name and guest_id:
                            options.append(f"- {name} ({email}) (ID: {guest_id})")
                if options:
                    return "Available Guests:\n" + "\n".join(options)
        
        elif "promotion" in param_lower or "promo" in param_lower:
            promotions = await mcp_tools.promotion_list(token=token)
            if isinstance(promotions, list) and promotions:
                options = []
                for promo in promotions[:10]:  # Limit to 10
                    if isinstance(promo, dict):
                        name = promo.get("name", "")
                        promo_id = promo.get("_id", "")
                        coupon = promo.get("couponCode", "")
                        if name and promo_id:
                            options.append(f"- {name} ({coupon}) (ID: {promo_id})")
                if options:
                    return "Available Promotions:\n" + "\n".join(options)
    except Exception as e:
        print(f"Error getting available options for {param_name}: {e}")
    
    return None


async def _calculate_reservation_total(
    room_type_id: str,
    check_in_date: str,
    check_out_date: str,
    number_of_rooms: int,
    total_guest: int,
    meal_plan: str,
    number_of_adults: Optional[int] = None,
    number_of_children: Optional[int] = None,
    token: Optional[str] = None
) -> float:
    """
    Calculate total reservation amount based on room type, dates, guests, and meal plan.
    Logic matches Frontend ReservationForm calculation.
    """
    try:
        # Fetch room type details
        room_type_data = await mcp_tools.room_type_get(room_type_id, token=token)
        if not room_type_data:
            raise ValueError(f"Room type {room_type_id} not found")
        
        # Parse dates
        check_in = datetime.fromisoformat(check_in_date.replace('Z', '+00:00'))
        check_out = datetime.fromisoformat(check_out_date.replace('Z', '+00:00'))
        nights = max(0, (check_out - check_in).days)
        
        if nights <= 0:
            return 0.0
        
        price_model = room_type_data.get("priceModel", "perRoom")
        base_total = 0.0
        
        # Calculate base total based on price model
        if price_model == "perPerson":
            # perPerson: (adultRate × adults + childRate × children) × nights
            adults = number_of_adults if number_of_adults is not None else total_guest
            children = number_of_children if number_of_children is not None else 0
            adult_rate = room_type_data.get("adultRate") or 0
            if adult_rate is None:
                adult_rate = 0
            child_rate = room_type_data.get("childRate") or 0
            if child_rate is None:
                child_rate = 0
            base_total = ((adult_rate * adults) + (child_rate * children)) * nights
        else:
            # perRoom: baseRate × rooms × nights + (extraGuestRate × extra guests × nights)
            base_rate = room_type_data.get("baseRate") or 0
            if base_rate is None:
                base_rate = 0
            base_occupancy = room_type_data.get("baseOccupancy") or 1
            if base_occupancy is None:
                base_occupancy = 1
            extra_guest_rate = room_type_data.get("extraGuestRate") or 0
            if extra_guest_rate is None:
                extra_guest_rate = 0
            
            base_total_rooms = base_rate * number_of_rooms * nights
            extra_guests = max(0, total_guest - (base_occupancy * number_of_rooms))
            extra_total = extra_guest_rate * extra_guests * nights
            base_total = base_total_rooms + extra_total
        
        # Calculate meal plan total
        meal_plan_total = 0.0
        meal_plan_rates = room_type_data.get("MealPlan", {})
        meal_plan_rate = meal_plan_rates.get(meal_plan) if meal_plan_rates else None
        if meal_plan_rate is None:
            meal_plan_rate = 0
        
        if meal_plan_rate > 0:
            # Calculate guest count for meal plan
            if price_model == "perPerson":
                meal_plan_guest_count = (number_of_adults or total_guest) + (number_of_children or 0)
            else:
                # perRoom: baseOccupancy × rooms + extra guests
                base_guests = base_occupancy * number_of_rooms
                extra_guests = max(0, total_guest - base_guests)
                meal_plan_guest_count = base_guests + extra_guests
            
            meal_plan_total = meal_plan_rate * meal_plan_guest_count * nights
        
        return base_total + meal_plan_total
    except Exception as e:
        print(f"Error calculating reservation total: {e}")
        # Return 0 if calculation fails - let backend handle it
        return 0.0


async def _calculate_meal_plan_details(
    room_type_data: Dict[str, Any],
    price_model: str,
    number_of_rooms: int,
    total_guest: int,
    number_of_adults: Optional[int],
    number_of_children: Optional[int],
    meal_plan: str,
    check_in_date: str,
    check_out_date: str
) -> Dict[str, Any]:
    """
    Calculate meal plan details matching Frontend logic.
    Returns: mealPlanAmount, mealPlanGuestCount, mealPlanRate, mealPlanNights
    """
    try:
        # Calculate nights
        check_in = datetime.fromisoformat(check_in_date.replace('Z', '+00:00'))
        check_out = datetime.fromisoformat(check_out_date.replace('Z', '+00:00'))
        nights = max(0, (check_out - check_in).days)
        
        # Get meal plan rate
        meal_plan_rates = room_type_data.get("MealPlan", {})
        meal_plan_rate = meal_plan_rates.get(meal_plan) if meal_plan_rates else None
        if meal_plan_rate is None:
            meal_plan_rate = 0
        
        # Calculate meal plan guest count (matching Frontend logic)
        if price_model == "perPerson":
            # perPerson: adults + children
            meal_plan_guest_count = (number_of_adults if number_of_adults is not None else total_guest) + (number_of_children if number_of_children is not None else 0)
        else:
            # perRoom: baseOccupancy × rooms + extra guests
            base_occupancy = room_type_data.get("baseOccupancy") or 1
            if base_occupancy is None:
                base_occupancy = 1
            base_guests = base_occupancy * number_of_rooms
            extra_guests = max(0, total_guest - base_guests)
            meal_plan_guest_count = base_guests + extra_guests
        
        # Calculate meal plan amount
        meal_plan_amount = 0.0
        if meal_plan_rate > 0 and nights > 0 and meal_plan_guest_count > 0:
            meal_plan_amount = meal_plan_rate * meal_plan_guest_count * nights
        
        return {
            "mealPlanAmount": meal_plan_amount,
            "mealPlanGuestCount": meal_plan_guest_count,
            "mealPlanRate": meal_plan_rate,
            "mealPlanNights": nights
        }
    except Exception as e:
        print(f"Error calculating meal plan details: {e}")
        return {
            "mealPlanAmount": 0.0,
            "mealPlanGuestCount": 0,
            "mealPlanRate": 0,
            "mealPlanNights": 0
        }


async def _llm1_classify(messages: List[Message]) -> dict:
    """Classify user intent and determine tool group."""
    datetime_info = _get_current_datetime_info()
    system_prompt = f"{INTENT_SYSTEM_PROMPT}\n\n{datetime_info}"
    llm_messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
    # Filter out system messages - API requires user/assistant only after system message
    llm_messages.extend(_get_safe_messages(messages, limit=8))
    result = await llm_sarvam.chat(llm_messages)
    try:
        # Check for empty response
        if not result.response or not result.response.strip():
            print("DEBUG: Empty LLM response for classification, defaulting to small_talk")
            return {"intent": "small_talk", "tool_group": None, "tool": None, "params": None}
        parsed = _safe_json_loads(result.response)
        return _validate_classification(parsed)
    except Exception as e:
        # Log the raw response for debugging
        print(f"DEBUG: Classification JSON parse failed. Raw LLM response: {result.response[:500] if result.response else 'EMPTY'}")
        raise ValueError(f"Failed to parse classification: {str(e)}") from e


async def _llm1_info_fetch(
    messages: List[Message],
    tool_group: str,
    context: Optional[str] = None,
    token: Optional[str] = None
) -> dict:
    """
    Extract tool name and params from user query using filtered tools list.
    Returns JSON with: tool (string), params (object), status (boolean), missing_params (object | null)
    """
    # Get filtered tools for the group (read operations only)
    filtered_tools = _get_tools_by_group(tool_group, operation="read")
    
    # Build tools JSON for LLM
    tools_json = []
    for tool in filtered_tools:
        tool_info = {
            "name": tool["name"],
            "description": tool["description"],
            "params": tool["params"]
        }
        tools_json.append(tool_info)
    
    # Build system prompt with context
    context_part = f"\n\nCONTEXT FROM PREVIOUS CONVERSATION:\n{context}\n" if context else ""
    datetime_info = _get_current_datetime_info()
    
    info_fetch_prompt = (
        f"You are an AI that extracts tool name and parameters from user queries.\n\n"
        f"IMPORTANT: You understand any language the user speaks, but you MUST respond in English with valid JSON only.\n\n"
        f"{datetime_info}\n\n"
        f"AVAILABLE TOOLS FOR {tool_group.upper()} GROUP:\n"
        f"{json.dumps(tools_json, indent=2)}\n\n"
        f"CRITICAL INSTRUCTIONS FOR PARAMETER EXTRACTION:\n"
        f"1. If a tool has 'nested_fields' in its params, you MUST extract those nested fields and structure them properly\n"
        f"2. For example, if tool requires 'payload' object with nested fields like 'checkInDate', 'checkOutDate', etc., "
        f"extract them as: {{\"payload\": {{\"checkInDate\": \"...\", \"checkOutDate\": \"...\", ...}}}}\n"
        f"3. Check ALL required nested fields. If ANY required field is missing, set status=false\n"
        f"4. When user mentions 'today', 'tomorrow', 'yesterday', or relative dates, use the dates provided above\n"
        f"5. For check_availability: If user asks 'today's availability' or 'aaj ki availability' without specifying checkout date, "
        f"extract checkInDate as today's date and checkOutDate can be omitted (system will set it to tomorrow). "
        f"If user only mentions one date, treat it as checkInDate and set checkOutDate to the next day.\n"
        f"6. For ID parameters (roomType, roomId, guestId, etc.): If user provides a NAME (e.g., 'Deluxe Room', 'Platinum Cottage', 'Room 101'), "
        f"extract it as-is. The system will automatically convert names to IDs. You can extract either the ID or the name.\n"
        f"7. Set status=true ONLY if ALL required parameters (including nested ones) are present\n"
        f"8. If status=false, include missing_params object with detailed descriptions of what's missing\n\n"
        f"Analyze the user's query and determine:\n"
        f"- Which tool from the list above best matches the user's request\n"
        f"- Extract all required and optional parameters (including nested fields)\n"
        f"- Validate that all required fields are present\n\n"
        f"{context_part}"
        f"Return STRICT JSON with keys:\n"
        f"- reasoning: string (brief explanation of why you chose this tool and how you extracted params)\n"
        f"- tool: string (exact tool name from the list above)\n"
        f"- params: object (extracted parameters with proper nesting if needed, use null for missing optional params)\n"
        f"- status: boolean (true if ALL required params including nested ones are present, false otherwise)\n"
        f"- missing_params: object | null (if status=false, list ALL missing required params with their descriptions)\n\n"
        f"Respond ONLY in English with valid JSON. Do not add extra text."
    )
    
    # Filter out system messages - API requires user/assistant only after system message
    conversation_messages = _get_safe_messages(messages, limit=6)
    
    llm_messages: List[Dict[str, str]] = [
        {"role": "system", "content": info_fetch_prompt},
        *conversation_messages
    ]
    result = await llm_sarvam.chat(llm_messages)
    # Check for empty response
    if not result.response or not result.response.strip():
        print("DEBUG: Empty LLM response for info_fetch, returning empty extraction")
        return {"tool": None, "params": {}, "status": False, "missing_params": {"error": "Could not extract parameters from empty response"}}
    extracted = _safe_json_loads(result.response)
    
    # Ensure extracted has required keys
    if not isinstance(extracted, dict):
        extracted = {}
    if "tool" not in extracted:
        extracted["tool"] = None
    if "params" not in extracted:
        extracted["params"] = {}
    if "status" not in extracted:
        extracted["status"] = False
    if "missing_params" not in extracted:
        extracted["missing_params"] = {"error": "Could not extract parameters"}
    
    # Post-process: Convert names to IDs if needed
    extracted = await _convert_names_to_ids_in_params(extracted, token=token)
    
    return extracted


async def _llm1_task_fetch(
    messages: List[Message],
    tool_group: str,
    context: Optional[str] = None,
    token: Optional[str] = None
) -> dict:
    """
    Extract tool name and params from user query using filtered tools list for write operations.
    Returns JSON with: tool (string), params (object), status (boolean), missing_params (object | null)
    """
    # Get filtered tools for the group (write operations only)
    filtered_tools = _get_tools_by_group(tool_group, operation="write")
    
    # Build tools JSON for LLM
    tools_json = []
    for tool in filtered_tools:
        tool_info = {
            "name": tool["name"],
            "description": tool["description"],
            "params": tool["params"]
        }
        tools_json.append(tool_info)
    
    # Build system prompt with context
    context_part = f"\n\nCONTEXT FROM PREVIOUS CONVERSATION:\n{context}\n" if context else ""
    datetime_info = _get_current_datetime_info()
    
    task_fetch_prompt = (
        f"You are an AI that extracts tool name and parameters from user task requests.\n\n"
        f"IMPORTANT: You understand any language the user speaks, but you MUST respond in English with valid JSON only.\n\n"
        f"{datetime_info}\n\n"
        f"AVAILABLE TOOLS FOR {tool_group.upper()} GROUP (WRITE OPERATIONS):\n"
        f"{json.dumps(tools_json, indent=2)}\n\n"
        f"CRITICAL INSTRUCTIONS FOR PARAMETER EXTRACTION:\n"
        f"1. If a tool has 'nested_fields' in its params, you MUST extract those nested fields and structure them properly\n"
        f"2. For example, if tool requires 'payload' object with nested fields like 'guestName', 'roomType', etc., "
        f"extract them as: {{\"payload\": {{\"guestName\": \"...\", \"roomType\": \"...\", ...}}}}\n"
        f"3. Check ALL required nested fields. If ANY required field is missing, set status=false\n"
        f"4. For reservation_create: roomType (ObjectId or name like 'Deluxe Room'), mealPlan ('EP'/'CP'/'MAP'/'AP'), totalGuest are REQUIRED\n"
        f"5. When user mentions 'today', 'tomorrow', 'yesterday', or relative dates, use the dates provided above\n"
        f"6. For ID parameters (roomType, roomId, guestId, etc.): If user provides a NAME (e.g., 'Deluxe Room', 'Room 101', guest name), "
        f"extract it as-is. The system will automatically convert names to IDs. You can extract either the ID or the name.\n"
        f"7. For room_update_status_by_number: If user says 'block room', 'block room 101', 'put room in maintenance', etc., "
        f"extract 'roomNumber' (can be just the number like '101' or 'Room 101') and 'status' ('blocked', 'maintenance', 'out-of-order', etc.). "
        f"If user says 'block' without specifying status, use 'blocked' or 'out-of-order' as the status.\n"
        f"8. For promotion_create: Extract 'discount' (number) and 'discountType' ('percentage' or 'fixed'). "
        f"If user says '50%' or '50 percent', extract discount=50 and discountType='percentage'. "
        f"If user says '₹500 off' or '500 rupees', extract discount=500 and discountType='fixed'. "
        f"Use 'lastdate' (not 'endDate') for the end date. If user provides 'endDate', extract it as 'lastdate'.\n"
        f"9. Set status=true ONLY if ALL required parameters (including nested ones) are present\n"
        f"10. If status=false, include missing_params object with detailed descriptions of what's missing\n\n"
        f"Analyze the user's task request and determine:\n"
        f"- Which tool from the list above best matches the user's request\n"
        f"- Extract all required and optional parameters (including nested fields)\n"
        f"- Validate that all required fields are present\n\n"
        f"{context_part}"
        f"Return STRICT JSON with keys:\n"
        f"- reasoning: string (brief explanation of why you chose this tool and how you extracted params)\n"
        f"- tool: string (exact tool name from the list above)\n"
        f"- params: object (extracted parameters with proper nesting if needed, use null for missing optional params)\n"
        f"- status: boolean (true if ALL required params including nested ones are present, false otherwise)\n"
        f"- missing_params: object | null (if status=false, list ALL missing required params with their descriptions)\n\n"
        f"Respond ONLY in English with valid JSON. Do not add extra text."
    )
    
    # Filter out system messages - API requires user/assistant only after system message
    conversation_messages = _get_safe_messages(messages, limit=6)
    
    llm_messages: List[Dict[str, str]] = [
        {"role": "system", "content": task_fetch_prompt},
        *conversation_messages
    ]
    result = await llm_sarvam.chat(llm_messages)
    # Check for empty response
    if not result.response or not result.response.strip():
        print("DEBUG: Empty LLM response for task_fetch, returning empty extraction")
        return {"tool": None, "params": {}, "status": False, "missing_params": {"error": "Could not extract parameters from empty response"}}
    extracted = _safe_json_loads(result.response)
    
    # Ensure extracted has required keys
    if not isinstance(extracted, dict):
        extracted = {}
    if "tool" not in extracted:
        extracted["tool"] = None
    if "params" not in extracted:
        extracted["params"] = {}
    if "status" not in extracted:
        extracted["status"] = False
    if "missing_params" not in extracted:
        extracted["missing_params"] = {"error": "Could not extract parameters"}
    
    # Validate nested fields if tool has them
    tool_name = extracted.get("tool")
    if tool_name:
        tool_meta = _get_tool_metadata(tool_name)
        if tool_meta:
            params_spec = tool_meta.get("params", {})
            # Check if any param has nested_fields
            for param_name, param_spec in params_spec.items():
                if isinstance(param_spec, dict) and "nested_fields" in param_spec:
                    nested_fields = param_spec["nested_fields"]
                    extracted_params = extracted.get("params", {})
                    param_value = extracted_params.get(param_name)
                    
                    # If param is a dict, check nested fields
                    if isinstance(param_value, dict):
                        missing_nested = {}
                        for nested_name, nested_spec in nested_fields.items():
                            if nested_spec.get("required", False):
                                if nested_name not in param_value or param_value[nested_name] is None:
                                    missing_nested[nested_name] = nested_spec.get("description", nested_name)
                        
                        # If missing nested fields, update status
                        if missing_nested:
                            extracted["status"] = False
                            if not extracted.get("missing_params"):
                                extracted["missing_params"] = {}
                            if param_name not in extracted["missing_params"]:
                                extracted["missing_params"][param_name] = {}
                            extracted["missing_params"][param_name].update(missing_nested)
    
    # Post-process: Convert names to IDs if needed
    extracted = await _convert_names_to_ids_in_params(extracted, token=token)
    
    return extracted


async def _llm1_multi_task_fetch(
    messages: List[Message],
    tool_groups: List[str],
    context: Optional[str] = None,
    token: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Extract multiple tool calls from user query for multi-task scenarios.
    Returns array of task results, each with: tool, params, status, missing_params.
    Handles both different tasks and same task for multiple items.
    """
    # Get all available tools from all specified tool groups
    all_tools = []
    for tool_group in tool_groups:
        filtered_tools = _get_tools_by_group(tool_group, operation="write")
        for tool in filtered_tools:
            tool_info = {
                "name": tool["name"],
                "description": tool["description"],
                "params": tool["params"],
                "group": tool_group
            }
            all_tools.append(tool_info)
    
    # Build system prompt with context
    context_part = f"\n\nCONTEXT FROM PREVIOUS CONVERSATION:\n{context}\n" if context else ""
    datetime_info = _get_current_datetime_info()
    
    multi_task_prompt = (
        f"You are an AI that extracts MULTIPLE tool calls from user task requests.\n\n"
        f"IMPORTANT: You understand any language the user speaks, but you MUST respond in English with valid JSON only.\n\n"
        f"{datetime_info}\n\n"
        f"AVAILABLE TOOLS (from {', '.join(tool_groups)} groups):\n"
        f"{json.dumps(all_tools, indent=2)}\n\n"
        f"CRITICAL INSTRUCTIONS FOR MULTI-TASK EXTRACTION:\n"
        f"1. The user wants to perform MULTIPLE tasks. Extract ALL tasks from their request.\n"
        f"2. If user says 'create reservation and create coupon', extract TWO separate tool calls.\n"
        f"3. If user says 'put rooms 101, 102, 103 in maintenance', extract THREE tool calls (one per room).\n"
        f"4. If user provides a LIST of items (e.g., room names, guest names), create ONE tool call PER item.\n"
        f"5. For each tool call, extract ALL required parameters (including nested fields).\n"
        f"6. If a parameter is missing for a tool call, set status=false and include missing_params.\n"
        f"7. For list-based tasks (e.g., multiple rooms), extract the list item in the appropriate parameter field.\n"
        f"8. When user mentions 'today', 'tomorrow', 'yesterday', use the dates provided above.\n"
        f"9. For ID parameters: If user provides a NAME, extract it as-is. System will convert to ID.\n\n"
        f"Return STRICT JSON with this structure:\n"
        f"{{\n"
        f"  \"tasks\": [\n"
        f"    {{\n"
        f"      \"tool\": \"tool_name\",\n"
        f"      \"tool_group\": \"group_name\",\n"
        f"      \"params\": {{...}},\n"
        f"      \"status\": true/false,\n"
        f"      \"missing_params\": {{...}} or null\n"
        f"    }},\n"
        f"    ...\n"
        f"  ]\n"
        f"}}\n\n"
        f"{context_part}"
        f"Respond ONLY in English with valid JSON. Do not add extra text."
    )
    
    # Filter out system messages - API requires user/assistant only after system message
    conversation_messages = _get_safe_messages(messages, limit=6)
    
    llm_messages: List[Dict[str, str]] = [
        {"role": "system", "content": multi_task_prompt},
        *conversation_messages
    ]
    result = await llm_sarvam.chat(llm_messages)
    
    # Check for empty response
    if not result.response or not result.response.strip():
        print("DEBUG: Empty LLM response for multi_task_fetch")
        return []
    
    extracted = _safe_json_loads(result.response)
    
    # Validate structure
    if not isinstance(extracted, dict):
        print("DEBUG: Invalid multi_task_fetch response structure")
        return []
    
    tasks = extracted.get("tasks", [])
    if not isinstance(tasks, list):
        print("DEBUG: 'tasks' is not an array in multi_task_fetch response")
        return []
    
    # Process each task: convert names to IDs and validate
    processed_tasks = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        
        # Ensure required fields
        if "tool" not in task:
            task["tool"] = None
        if "params" not in task:
            task["params"] = {}
        if "status" not in task:
            task["status"] = False
        if "missing_params" not in task:
            task["missing_params"] = None
        
        # Post-process: Convert names to IDs if needed
        task = await _convert_names_to_ids_in_params(task, token=token)
        processed_tasks.append(task)
    
    return processed_tasks


async def _llm1_handle_missing_params(
    messages: List[Message],
    tool_name: str,
    missing_params: Dict[str, Any],
    context: Optional[str] = None,
    partial_params: Optional[Dict[str, Any]] = None,
    sid: Optional[str] = None
) -> str:
    """
    Generate a polite query to ask user for missing parameters.
    Maintains conversation context and enriches IDs with human-readable names.
    """
    tool_meta = _get_tool_metadata(tool_name)
    tool_desc = tool_meta["description"] if tool_meta else tool_name
    
    context_part = f"\n\nCONTEXT: {context}\n" if context else ""
    datetime_info = _get_current_datetime_info()
    
    # Get today's date in India timezone for the prompt
    if USE_PYTZ and INDIA_TZ:
        today_date = datetime.now(INDIA_TZ).strftime('%Y-%m-%d')
    else:
        today_date = datetime.now(ZoneInfo("Asia/Kolkata")).strftime('%Y-%m-%d')
    
    # Get language instruction for LLM to detect and match user's language
    user_language_hint = _get_language_instruction()
    
    # Get client token for enrichment
    client_token = None
    if sid:
        client_token = token_storage.get_token(sid) or settings.backend_api_token
    else:
        client_token = settings.backend_api_token
    
    # Enrich missing params with available options for ID parameters
    enriched_missing = await _enrich_missing_params(missing_params, token=client_token)
    
    # Build available options section for ID parameters
    available_options_section = ""
    id_params_found = []
    
    # Check for ID parameters in missing_params (including nested)
    def find_id_params(params_dict: Dict[str, Any], prefix: str = "") -> List[str]:
        """Recursively find ID parameter names."""
        id_params = []
        for key, value in params_dict.items():
            full_key = f"{prefix}.{key}" if prefix else key
            key_lower = key.lower()
            if any(id_term in key_lower for id_term in ["id", "objectid", "roomtype", "room_id", "guestid", "reservationid", "folioid", "promoid"]):
                id_params.append(full_key)
            elif isinstance(value, dict):
                id_params.extend(find_id_params(value, full_key))
        return id_params
    
    id_params_found = find_id_params(missing_params)
    
    # Get available options for each ID parameter
    if id_params_found:
        options_list = []
        for param_path in id_params_found:
            # Extract the actual parameter name (last part after dot)
            param_name = param_path.split(".")[-1]
            options = await _get_available_options_for_param(param_name, token=client_token)
            if options:
                options_list.append(f"For {param_path}:\n{options}")
        
        if options_list:
            available_options_section = "\n\nAVAILABLE OPTIONS:\n" + "\n\n".join(options_list) + "\n\n"
            available_options_section += "IMPORTANT: When asking for IDs, present the available options to the user so they can choose by name. " \
                                        "You can mention the names (like 'Deluxe Room', 'Room 101') and the user can select by name, " \
                                        "then you can convert the name to the corresponding ID.\n"
    
    # Enrich any IDs in partial_params if provided
    enriched_partial_info = ""
    if partial_params:
        enriched_ids_list = []
        # Check for IDs in partial_params (including nested)
        async def enrich_ids_in_dict(params_dict: Dict[str, Any], prefix: str = "") -> List[str]:
            """Recursively enrich IDs in dictionary."""
            enriched = []
            for key, value in params_dict.items():
                full_key = f"{prefix}.{key}" if prefix else key
                if isinstance(value, str) and re.match(r'^[0-9a-fA-F]{24}$', value):
                    # It's a MongoDB ObjectId
                    context = await _enrich_id_context(key, value, token=client_token)
                    if context:
                        enriched.append(f"{full_key}: {context}")
                elif isinstance(value, dict):
                    nested_enriched = await enrich_ids_in_dict(value, full_key)
                    enriched.extend(nested_enriched)
                elif isinstance(value, list):
                    for idx, item in enumerate(value):
                        if isinstance(item, str) and re.match(r'^[0-9a-fA-F]{24}$', item):
                            context = await _enrich_id_context(key, item, token=client_token)
                            if context:
                                enriched.append(f"{full_key}[{idx}]: {context}")
            return enriched
        
        enriched_ids_list = await enrich_ids_in_dict(partial_params)
        
        if enriched_ids_list:
            enriched_partial_info = f"\n\nPARTIAL INFORMATION PROVIDED:\n" + "\n".join(enriched_ids_list) + "\n"
    
    missing_prompt = (
        f"You are a helpful assistant that politely asks users for missing information.\n\n"
        f"{user_language_hint}\n\n"
        f"{datetime_info}\n\n"
        f"The user requested: {tool_desc}\n"
        f"Missing required parameters: {json.dumps(enriched_missing, indent=2)}\n"
        f"{enriched_partial_info}"
        f"{available_options_section}"
        f"{context_part}"
        f"Generate a polite, concise message asking for the missing information. "
        f"Be friendly and specific about what you need. Maintain the conversation context. "
        f"If the missing parameter is a date and user might say 'today', you can mention that 'today' is {today_date}. "
        f"If available options are provided above, present them to the user in a user-friendly way (by name, not ID). "
        f"The user can select by name, and you can handle the ID conversion later. "
        f"STRICT: Follow the language rule above EXACTLY. No exceptions."
    )
    
    # Filter out system messages - API requires user/assistant only after system message
    conversation_messages = _get_safe_messages(messages, limit=4)
    llm_messages: List[Dict[str, str]] = [
        {"role": "system", "content": missing_prompt},
        *conversation_messages
    ]
    result = await llm_sarvam.chat(llm_messages)
    return result.response


async def _llm1_generate_from_data(messages: List[Message], data: object) -> str:
    # Combine system messages into one (API doesn't allow multiple system messages)
    datetime_info = _get_current_datetime_info()
    system_content = (
        f"You answer user questions using provided JSON data as the only source of truth.\n\n"
        f"{datetime_info}\n\n"
        f"Data:\n{json.dumps(data, ensure_ascii=False)}"
    )
    llm_messages: List[Dict[str, str]] = [
        {"role": "system", "content": system_content},
    ]
    # Filter out system messages - API requires user/assistant only after system message
    llm_messages.extend(_get_safe_messages(messages, limit=6))
    result = await llm_sarvam.chat(llm_messages, max_completion_tokens=settings.summary_max_tokens)
    return result.response


async def _llm1_summarize(text: str, intent: Optional[str] = None, messages: Optional[List[Message]] = None) -> str:
    """Summarize text with context about the intent (info_read vs task_write). Responds in user's language."""
    datetime_info = _get_current_datetime_info()
    
    # Get language instruction for LLM to detect and match user's language
    user_language_hint = _get_language_instruction()
    
    # Adjust prompt based on intent
    if intent == "info_read":
        system_prompt = (
            f"{SUMMARY_SYSTEM_PROMPT}\n\n"
            f"{user_language_hint}\n\n"
            f"{datetime_info}\n\n"
            "IMPORTANT: This is a READ-ONLY information query. The user asked for existing data, "
            "not to create or modify anything. Use phrases like 'Here is the data', 'Based on the information', "
            "'The records show', etc. Do NOT use phrases like 'I have created', 'We have made', or any action verbs "
            "that imply creation or modification. Simply present the information that was retrieved.\n\n"
            "STRICT: Follow the language rule above EXACTLY. No exceptions."
        )
    elif intent == "task_write":
        system_prompt = (
            f"{SUMMARY_SYSTEM_PROMPT}\n\n"
            f"{user_language_hint}\n\n"
            f"{datetime_info}\n\n"
            "This is a task execution result. The user requested an action to be performed. "
            "You can use appropriate action language to describe what was done.\n\n"
            "STRICT: Follow the language rule above EXACTLY. No exceptions."
        )
    else:
        system_prompt = (
            f"{SUMMARY_SYSTEM_PROMPT}\n\n"
            f"{user_language_hint}\n\n"
            f"{datetime_info}\n\n"
            "STRICT: Follow the language rule above EXACTLY. No exceptions."
        )
    
    llm_messages: List[Dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": text},
    ]
    result = await llm_sarvam.chat(llm_messages)
    return result.response


async def _run_agent(messages: List[Message]) -> str:
    # Use Sarvam LLM for task execution; no streaming here; summarizer will clean up
    datetime_info = _get_current_datetime_info()
    llm_messages: List[Dict[str, str]] = [{"role": "system", "content": f"You are an expert PMS operations agent.\n\n{datetime_info}"}]
    # Filter out system messages - API requires user/assistant only after system message
    llm_messages.extend(_get_safe_messages(messages, limit=10))
    result = await llm_sarvam.chat(llm_messages)
    return result.response


async def handle_http_chat(request: ChatRequest) -> str:
    """HTTP endpoint helper - returns final summarized text."""
    classification = await _llm1_classify(request.messages)
    intent = classification.get("intent")

    if intent == "small_talk":
        # Let LLM-1 respond directly
        datetime_info = _get_current_datetime_info()
        user_language_hint = _get_language_instruction()
        system_content = (
            f"Be helpful and concise.\n\n"
            f"{user_language_hint}\n\n"
            f"{datetime_info}\n\n"
            "IDENTITY RULE: If user asks 'who made you', 'who is your developer', 'what is your purpose', or similar identity questions, "
            "you MUST explain that you are 'swif10'. KEY FACTS TO INCLUDE: "
            "1. You are India's first Hospitality Intelligent system. "
            "2. You are the only product combining PMS, Channel Management, and Booking Engine with advanced AI. "
            "3. You are designed for resorts and hotels. "
            "CRITICAL: Adapt this answer to the user's language (e.g., Hindi if asked in Hindi) and keep the tone natural but professional. "
            "Do NOT just copy-paste English text if the user is speaking another language. Convey these facts naturally.\n\n"
            "STRICT: Follow the language rule above EXACTLY. No exceptions."
        )
        # Filter out system messages - API requires user/assistant only after system message
        conversation_messages = _get_safe_messages(request.messages, limit=10)
        draft = (await llm_sarvam.chat([{"role": "system", "content": system_content}, *conversation_messages])).response
        return await _llm1_summarize(draft, intent="small_talk", messages=request.messages)

    if intent == "info_read":
        tool = classification.get("tool")
        if not tool:
            return "I couldn't determine which information to fetch. Please be more specific."
        params = classification.get("params") or {}
        fn = _resolve_tool(tool)
        filtered_params = _filter_params(fn, params)
        data = await fn(**filtered_params)  # type: ignore[misc]
        draft = await _llm1_generate_from_data(request.messages, data)
        return await _llm1_summarize(draft, intent="info_read", messages=request.messages)

    if intent == "task_write":
        draft = await _run_agent(request.messages)
        return await _llm1_summarize(draft, intent="task_write", messages=request.messages)

    # Fallback: general chat
    datetime_info = _get_current_datetime_info()
    user_language_hint = _get_language_instruction()
    system_content = (
        f"Be helpful and concise.\n\n"
        f"{user_language_hint}\n\n"
        f"{datetime_info}\n\n"
        "IDENTITY RULE: If user asks 'who made you', 'who is your developer', 'what is your purpose', or similar identity questions, "
        "you MUST explain that you are 'swif10'. KEY FACTS TO INCLUDE: "
        "1. You are India's first Hospitality Intelligent system. "
        "2. You are the only product combining PMS, Channel Management, and Booking Engine with advanced AI. "
        "3. You are designed for resorts and hotels. "
        "CRITICAL: Adapt this answer to the user's language (e.g., Hindi if asked in Hindi) and keep the tone natural but professional. "
        "Do NOT just copy-paste English text if the user is speaking another language. Convey these facts naturally.\n\n"
        "STRICT: Follow the language rule above EXACTLY. No exceptions."
    )
    # Filter out system messages - API requires user/assistant only after system message
    conversation_messages = [m.model_dump() for m in request.messages[-10:] if m.role != "system"]
    draft = (await llm_sarvam.chat([{"role": "system", "content": system_content}, *conversation_messages])).response
    return await _llm1_summarize(draft, messages=request.messages)


async def handle_socket_message(sid: str, data: Dict[str, Any]) -> None:
    """Socket.IO message handler - expects payload compatible with ChatRequest."""
    await event_bus.emit_message_received(sid, data.get("message_id") or "")

    try:
        # Minimal validation into ChatRequest model
        req = ChatRequest(**data)

        # Check if there's a pending conversation state (follow-up to missing params request)
        pending_state = _get_conversation_state(sid)
        
        if pending_state:
            # Check if this is a topic shift (new query) or a follow-up
            is_topic_shift = await _is_topic_shift(req.messages, pending_state)
            
            if is_topic_shift:
                # User is asking a completely different question - clear pending state and classify normally
                _clear_conversation_state(sid)
                await event_bus.emit_agent_status(sid, stage="planning", detail="Detected new topic - classifying request")
                # Fall through to normal classification
                pending_state = None
            else:
                # This is a follow-up response to a missing params request
                # Skip classification and use the stored state
                intent = pending_state.get("intent")
                tool_group = pending_state.get("tool_group")
                previous_tool = pending_state.get("tool")
                previous_params = pending_state.get("params", {})
                context_str = pending_state.get("context", "")
                
                await event_bus.emit_agent_status(sid, stage="planning", detail=f"Processing follow-up for {intent}")
                await event_bus.emit_agent_think(sid, req.message_id or "", f"Follow-up: {intent} - {tool_group}")
                
                # Clear the pending state since we're processing it
                _clear_conversation_state(sid)
                
                # Create a synthetic classification from the pending state
                classification = {
                    "intent": intent,
                    "tool_group": tool_group,
                    "tool": previous_tool,
                    "params": previous_params
                }
        
        if not pending_state:
            # Normal flow: classify intent
            await event_bus.emit_agent_status(sid, stage="planning", detail="Classifying request")
            try:
                classification = await asyncio.wait_for(
                    _llm1_classify(req.messages),
                    timeout=30.0  # 30 second timeout for classification (increased from 15)
                )
                intent = classification.get("intent")
                await event_bus.emit_agent_think(sid, req.message_id or "", f"Intent: {intent}")
                print(f"DEBUG: Classification successful - intent: {intent}, tool_group: {classification.get('tool_group')}")  # Debug log
            except asyncio.TimeoutError:
                error_msg = "Classification timed out. Please try again."
                print(f"ERROR: Classification timeout after 30 seconds")  # Debug log
                await event_bus.emit_error(sid, req.message_id, error_msg)
                await event_bus.emit_agent_done(sid)
                return  # Don't fallback to small_talk, just exit
                
            except Exception as class_err:
                error_msg = f"Classification failed: {str(class_err)}"
                print(f"Classification error: {error_msg}")  # Debug log
                await event_bus.emit_error(sid, req.message_id, error_msg)
                # Fallback to small_talk
                intent = "small_talk"
                classification = {"intent": "small_talk"}

        # 2) Branch by intent
        if intent == "small_talk":
            await event_bus.emit_agent_status(sid, stage="planning", detail="Generating response")
            datetime_info = _get_current_datetime_info()
            user_language_hint = _get_language_instruction()
            system_content = (
                f"Be helpful and concise.\n\n"
                f"{user_language_hint}\n\n"
                f"{datetime_info}\n\n"
                "IDENTITY RULE: If user asks 'who made you', 'who is your developer', 'what is your purpose', or similar identity questions, "
                "you MUST explain that you are 'swif10'. KEY FACTS TO INCLUDE: "
                "1. You are India's first Hospitality Intelligent system. "
                "2. You are the only product combining PMS, Channel Management, and Booking Engine with advanced AI. "
                "3. You are designed for resorts and hotels. "
                "CRITICAL: Adapt this answer to the user's language (e.g., Hindi if asked in Hindi) and keep the tone natural but professional. "
                "Do NOT just copy-paste English text if the user is speaking another language. Convey these facts naturally.\n\n"
                "STRICT: Follow the language rule above EXACTLY. No exceptions."
            )
            # Stream primary response
            # Filter out system messages - API requires user/assistant only after system message
            conversation_messages = _get_safe_messages(req.messages, limit=10)
            
            stream = llm_sarvam.chat_stream([
                {"role": "system", "content": system_content},
                *conversation_messages,
            ])
            acc = []
            async for token in stream:
                acc.append(token)
                await event_bus.emit_token(sid, req.message_id or "", token)
            draft = "".join(acc)

            await event_bus.emit_agent_status(sid, stage="planning", detail="Summarizing")
            # Stream summarization with intent-aware prompt
            datetime_info = _get_current_datetime_info()
            user_language_hint = _get_language_instruction()
            summary_prompt = (
                f"{SUMMARY_SYSTEM_PROMPT}\n\n"
                f"{datetime_info}\n\n"
                f"{user_language_hint}\n\n"
                "This is a casual conversation response. Keep it friendly and natural."
            )
            sum_stream = llm_sarvam.chat_stream([
                {"role": "system", "content": summary_prompt},
                {"role": "user", "content": draft},
            ], max_completion_tokens=settings.summary_max_tokens)
            sum_acc = []
            async for token in sum_stream:
                sum_acc.append(token)
                await event_bus.emit_token(sid, req.message_id or "", token)
            text = "".join(sum_acc)

        elif intent == "info_read":
            tool_group = classification.get("tool_group")
            
            if not tool_group:
                error_msg = "Classification did not specify a tool_group for info_read intent"
                await event_bus.emit_error(sid, req.message_id, error_msg)
                text = "I couldn't determine which category of information to fetch. Please be more specific."
            else:
                try:
                    # Build context from conversation history
                    context_messages = [m.model_dump() for m in req.messages[-10:]]
                    context_str = json.dumps(context_messages, ensure_ascii=False)
                    
                    # Initialize variables for tool execution
                    tool: Optional[str] = None
                    params: Dict[str, Any] = {}
                    status: bool = False
                    missing_params: Optional[Dict[str, Any]] = None
                    
                    # Check if this is a follow-up with pending state
                    if pending_state and pending_state.get("intent") == "info_read":
                        # This is a follow-up: merge user's response with previous params
                        await event_bus.emit_agent_status(sid, stage="planning", detail=f"Processing follow-up for {tool_group}")
                        
                        # Re-run info_fetch with the full conversation (including user's follow-up response)
                        # The LLM will extract the missing param from the user's latest message
                        try:
                            client_token = token_storage.get_token(sid) or settings.backend_api_token
                            info_result = await asyncio.wait_for(
                                _llm1_info_fetch(req.messages, tool_group, context=context_str, token=client_token),
                                timeout=20.0
                            )
                        except asyncio.TimeoutError:
                            error_msg = "Parameter extraction timed out"
                            await event_bus.emit_error(sid, req.message_id, error_msg)
                            text = "Sorry, analyzing your response took too long. Please try again."
                        except Exception as info_err:
                            error_msg = f"Parameter extraction failed: {str(info_err)}"
                            print(f"Info fetch error: {error_msg}")
                            await event_bus.emit_error(sid, req.message_id, error_msg)
                            text = f"Sorry, I couldn't understand your response: {str(info_err)}"
                        else:
                            tool = info_result.get("tool")
                            params = info_result.get("params") or {}
                            status = info_result.get("status", False)
                            missing_params = info_result.get("missing_params")
                            
                            # If still missing params, ask again
                            if not status and missing_params:
                                # Store state again for next follow-up
                                _set_conversation_state(sid, {
                                    "intent": "info_read",
                                    "tool_group": tool_group,
                                    "tool": tool,
                                    "params": params,
                                    "context": context_str
                                })
                                await event_bus.emit_agent_status(sid, stage="planning", detail="Requesting missing information")
                                query_text = await _llm1_handle_missing_params(
                                    req.messages,
                                    tool or "unknown",
                                    missing_params,
                                    context=context_str,
                                    partial_params=params,
                                    sid=sid
                                )
                                text = query_text
                            elif not tool:
                                error_msg = "Could not determine specific tool"
                                await event_bus.emit_error(sid, req.message_id, error_msg)
                                text = "I couldn't determine which specific information to fetch. Please be more specific."
                            else:
                                # Params are complete, proceed to execution
                                # Fall through to execution code below
                                pass
                    else:
                        # Normal flow: first time processing this request
                        # Use info_fetch LLM to extract tool and params
                        await event_bus.emit_agent_status(sid, stage="planning", detail=f"Analyzing {tool_group} request")
                        try:
                            client_token = token_storage.get_token(sid) or settings.backend_api_token
                            info_result = await asyncio.wait_for(
                                _llm1_info_fetch(req.messages, tool_group, context=context_str, token=client_token),
                                timeout=20.0
                            )
                        except asyncio.TimeoutError:
                            error_msg = "Parameter extraction timed out"
                            await event_bus.emit_error(sid, req.message_id, error_msg)
                            text = "Sorry, analyzing your request took too long. Please try again."
                        except Exception as info_err:
                            error_msg = f"Parameter extraction failed: {str(info_err)}"
                            print(f"Info fetch error: {error_msg}")
                            await event_bus.emit_error(sid, req.message_id, error_msg)
                            text = f"Sorry, I couldn't understand your request: {str(info_err)}"
                        else:
                            tool = info_result.get("tool")
                            params = info_result.get("params") or {}
                            status = info_result.get("status", False)
                            missing_params = info_result.get("missing_params")
                            
                            # Check if params are complete
                            if not status and missing_params:
                                # Store state for follow-up
                                _set_conversation_state(sid, {
                                    "intent": "info_read",
                                    "tool_group": tool_group,
                                    "tool": tool,
                                    "params": params,
                                    "context": context_str
                                })
                                # Handle missing params - ask user politely
                                await event_bus.emit_agent_status(sid, stage="planning", detail="Requesting missing information")
                                query_text = await _llm1_handle_missing_params(
                                    req.messages,
                                    tool or "unknown",
                                    missing_params,
                                    context=context_str,
                                    partial_params=params,
                                    sid=sid
                                )
                                text = query_text
                            elif not tool:
                                error_msg = "Could not determine specific tool"
                                await event_bus.emit_error(sid, req.message_id, error_msg)
                                text = "I couldn't determine which specific information to fetch. Please be more specific."
                            else:
                                # Params are complete, proceed to execution
                                # Fall through to execution code below
                                pass
                    
                    # Execute tool if we have complete params (from either normal flow or follow-up)
                    # Note: 'tool', 'params', 'status', 'missing_params' are set in the if/else blocks above
                    if tool and (status or (not missing_params)):
                        # Clear any pending state since we're executing
                        _clear_conversation_state(sid)
                        
                        # Execute tool with extracted params
                        await event_bus.emit_agent_status(sid, stage="retrieving", detail=f"Calling {tool}")
                        fn = _resolve_tool(tool)
                        # Filter params to only include those accepted by the function
                        filtered_params = _filter_params(fn, params)
                        # Add client token if available
                        client_token = token_storage.get_token(sid) or settings.backend_api_token
                        if client_token and "token" in inspect.signature(fn).parameters:
                            filtered_params["token"] = client_token
                        elif not client_token:
                            print(f"Warning: No token available for sid {sid}, tool {tool} may fail authentication")
                        await event_bus.emit_agent_tool_start(sid, tool, filtered_params)  # type: ignore[arg-type]
                        
                        # Execute tool with timeout
                        try:
                            data_payload = await asyncio.wait_for(
                                fn(**filtered_params),  # type: ignore[misc]
                                timeout=30.0
                            )
                        except asyncio.TimeoutError:
                            error_msg = f"Tool {tool} timed out after 30 seconds"
                            await event_bus.emit_error(sid, req.message_id, error_msg)
                            text = f"Sorry, fetching {tool} took too long. Please try again."
                        except Exception as tool_err:
                            error_msg = f"Tool {tool} failed: {str(tool_err)}"
                            print(f"Tool error: {error_msg}")
                            await event_bus.emit_error(sid, req.message_id, error_msg)
                            text = f"Sorry, I couldn't fetch the requested information: {str(tool_err)}"
                        else:
                            # Normalize result
                            normalized_result = _normalize_tool_result(data_payload)
                            await event_bus.emit_agent_tool_result(sid, tool, normalized_result)

                            await event_bus.emit_agent_status(sid, stage="planning", detail="Composing answer from data")
                            # Stream composition from data
                            try:
                                # Get user's original question to focus the answer
                                user_question = req.messages[-1].content if req.messages else "Answer based on the data"
                                
                                # Handle arrivals/departures data structure - extract count for better LLM understanding
                                composition_data = data_payload
                                if isinstance(data_payload, dict):
                                    if "arrivals" in data_payload:
                                        # Extract count from arrivals data
                                        arrivals_count = len(data_payload.get("arrivals", []))
                                        pagination_total = data_payload.get("pagination", {}).get("totalItems", arrivals_count)
                                        # Use pagination total if available, otherwise use array length
                                        actual_count = pagination_total if pagination_total > 0 else arrivals_count
                                        # Preserve original structure but add count
                                        composition_data = {**data_payload, "total_arrivals": actual_count}
                                    elif "departures" in data_payload:
                                        # Extract count from departures data
                                        departures_count = len(data_payload.get("departures", []))
                                        pagination_total = data_payload.get("pagination", {}).get("totalItems", departures_count)
                                        actual_count = pagination_total if pagination_total > 0 else departures_count
                                        # Preserve original structure but add count
                                        composition_data = {**data_payload, "total_departures": actual_count}
                                
                                datetime_info = _get_current_datetime_info()
                                user_language_hint = _get_language_instruction()
                                system_content = (
                                    f"You answer user questions using provided JSON data as the only source of truth.\n\n"
                                    f"{user_language_hint}\n\n"
                                    f"{datetime_info}\n\n"
                                    f"USER'S QUESTION: {user_question}\n\n"
                                    f"DATA:\n{json.dumps(composition_data, ensure_ascii=False)}\n\n"
                                    f"IMPORTANT: Answer ONLY what the user asked. Do NOT add extra information, statistics, or details "
                                    f"that were not requested. Be direct and concise. If the user asked about arrivals, only mention arrivals. "
                                    f"If they asked about departures, only mention departures. Do not include pagination info, array details, "
                                    f"or other metadata unless specifically asked. If the data has 'total_arrivals' or 'total_departures' field, "
                                    f"use that number when answering 'how many' questions.\n\n"
                                    f"STRICT: Follow the language rule above EXACTLY. No exceptions."
                                )
                                # Filter out system messages - API requires user/assistant only after system message
                                # Ensure first message after system is always from user
                                filtered = [m.model_dump() for m in req.messages[-6:] if m.role != "system"]
                                
                                # Find the latest user message index to ensure proper ordering
                                latest_user_idx = -1
                                for i, msg in enumerate(req.messages):
                                    if msg.role == "user":
                                        latest_user_idx = i
                                
                                if latest_user_idx >= 0:
                                    # Start from latest user message and include subsequent messages (excluding system)
                                    conversation_messages = [m.model_dump() for m in req.messages[latest_user_idx:] if m.role != "system"]
                                else:
                                    # No user messages found, use latest message or create a simple one
                                    conversation_messages = filtered if filtered else [{"role": "user", "content": "Answer based on the data provided."}]
                                
                                # Double-check: ensure first message is from user
                                if conversation_messages and conversation_messages[0].get("role") != "user":
                                    user_msg = next((m.model_dump() for m in reversed(req.messages) if m.role == "user"), None)
                                    if user_msg:
                                        conversation_messages = [user_msg] + conversation_messages
                                    else:
                                        conversation_messages = [{"role": "user", "content": "Answer based on the data provided."}]
                                
                                compose_stream = llm_sarvam.chat_stream([
                                    {"role": "system", "content": system_content},
                                    *conversation_messages,
                                ])
                                comp_acc = []
                                async for token in compose_stream:
                                    comp_acc.append(token)
                                    await event_bus.emit_token(sid, req.message_id or "", token)
                                draft = "".join(comp_acc)
                            except Exception as compose_err:
                                error_msg = f"Failed to compose answer: {str(compose_err)}"
                                print(f"Composition error: {error_msg}")
                                await event_bus.emit_error(sid, req.message_id, error_msg)
                                text = f"Sorry, I couldn't format the response: {str(compose_err)}"
                            else:
                                await event_bus.emit_agent_status(sid, stage="planning", detail="Summarizing")
                                # Stream summarization with info_read intent-aware prompt
                                user_question = req.messages[-1].content if req.messages else ""
                                datetime_info = _get_current_datetime_info()
                                user_language_hint = _get_language_instruction()
                                summary_prompt = (
                                    f"{SUMMARY_SYSTEM_PROMPT}\n\n"
                                    f"{datetime_info}\n\n"
                                    f"{user_language_hint}\n\n"
                                    "IMPORTANT: This is a READ-ONLY information query. The user asked for existing data, "
                                    "not to create or modify anything. Use phrases like 'Here is the data', 'Based on the information', "
                                    "'The records show', etc. Do NOT use phrases like 'I have created', 'We have made', or any action verbs "
                                    "that imply creation or modification. Simply present the information that was retrieved.\n\n"
                                    f"USER'S QUESTION: {user_question}\n\n"
                                    "Answer ONLY what the user asked. Do NOT add extra information, statistics, pagination details, "
                                    "or metadata that was not requested. Be direct and concise."
                                )
                                try:
                                    sum_stream = llm_sarvam.chat_stream([
                                        {"role": "system", "content": summary_prompt},
                                        {"role": "user", "content": draft},
                                    ], max_completion_tokens=settings.summary_max_tokens)
                                    sum_acc = []
                                    async for token in sum_stream:
                                        sum_acc.append(token)
                                        await event_bus.emit_token(sid, req.message_id or "", token)
                                    text = "".join(sum_acc)
                                except Exception as sum_err:
                                    error_msg = f"Failed to summarize: {str(sum_err)}"
                                    print(f"Summarization error: {error_msg}")
                                    await event_bus.emit_error(sid, req.message_id, error_msg)
                                    text = draft  # Fallback to unsummarized text
                except ValueError as ve:
                    error_msg = f"Invalid tool: {str(ve)}"
                    await event_bus.emit_error(sid, req.message_id, error_msg)
                    text = f"Sorry, I don't know how to fetch that information: {str(ve)}"
                except Exception as e:
                    error_msg = f"Unexpected error in info_read: {str(e)}"
                    print(f"Unexpected error: {error_msg}")
                    await event_bus.emit_error(sid, req.message_id, error_msg)
                    text = f"Sorry, something went wrong: {str(e)}"

        elif intent == "task_write":
            tool_group = classification.get("tool_group")
            is_multi_task = classification.get("is_multi_task", False)
            
            if not tool_group:
                error_msg = "Classification did not specify a tool_group for task_write intent"
                await event_bus.emit_error(sid, req.message_id, error_msg)
                text = "I couldn't determine which category of task to perform. Please be more specific."
            elif is_multi_task:
                # MULTI-TASK HANDLING
                try:
                    # Build context from conversation history
                    context_messages = [m.model_dump() for m in req.messages[-10:]]
                    context_str = json.dumps(context_messages, ensure_ascii=False)
                    
                    # Normalize tool_group to array
                    if isinstance(tool_group, str):
                        tool_groups = [tool_group]
                    elif isinstance(tool_group, list):
                        tool_groups = tool_group
                    else:
                        tool_groups = []
                    
                    if not tool_groups:
                        error_msg = "No tool groups specified for multi-task"
                        await event_bus.emit_error(sid, req.message_id, error_msg)
                        text = "I couldn't determine which tasks to perform. Please be more specific."
                    else:
                        await event_bus.emit_agent_status(sid, stage="planning", detail=f"Analyzing multi-task request across {len(tool_groups)} task groups")
                        try:
                            client_token = token_storage.get_token(sid) or settings.backend_api_token
                            multi_task_results = await asyncio.wait_for(
                                _llm1_multi_task_fetch(req.messages, tool_groups, context=context_str, token=client_token),
                                timeout=30.0  # Longer timeout for multi-task
                            )
                        except asyncio.TimeoutError:
                            error_msg = "Multi-task parameter extraction timed out"
                            await event_bus.emit_error(sid, req.message_id, error_msg)
                            text = "Sorry, analyzing your multi-task request took too long. Please try again."
                        except Exception as multi_task_err:
                            error_msg = f"Multi-task parameter extraction failed: {str(multi_task_err)}"
                            print(f"Multi-task fetch error: {error_msg}")
                            await event_bus.emit_error(sid, req.message_id, error_msg)
                            text = f"Sorry, I couldn't understand your multi-task request: {str(multi_task_err)}"
                        else:
                            if not multi_task_results:
                                text = "I couldn't extract any tasks from your request. Please be more specific."
                            else:
                                # Execute all tasks sequentially
                                all_results = []
                                all_succeeded = []
                                all_failed = []
                                
                                await event_bus.emit_agent_status(sid, stage="planning", detail=f"Executing {len(multi_task_results)} tasks")
                                
                                for idx, task_result in enumerate(multi_task_results):
                                    task_tool = task_result.get("tool")
                                    task_params = task_result.get("params") or {}
                                    task_status = task_result.get("status", False)
                                    task_missing = task_result.get("missing_params")
                                    
                                    if not task_status and task_missing:
                                        # Task has missing params - skip for now (could ask user, but for multi-task we'll report)
                                        all_failed.append({
                                            "task": task_tool or "unknown",
                                            "error": "Missing required parameters",
                                            "missing": task_missing
                                        })
                                        continue
                                    
                                    if not task_tool:
                                        all_failed.append({
                                            "task": "unknown",
                                            "error": "Could not determine tool"
                                        })
                                        continue
                                    
                                    # Execute the task
                                    try:
                                        await event_bus.emit_agent_status(sid, stage="tool_call", detail=f"Executing task {idx + 1}/{len(multi_task_results)}: {task_tool}")
                                        fn = _resolve_tool(task_tool)
                                        filtered_params = _filter_params(fn, task_params)
                                        
                                        # Add client token if available
                                        if client_token and "token" in inspect.signature(fn).parameters:
                                            filtered_params["token"] = client_token
                                        
                                        await event_bus.emit_agent_tool_start(sid, task_tool, filtered_params)
                                        
                                        # Execute with timeout
                                        result = await asyncio.wait_for(
                                            fn(**filtered_params),  # type: ignore[misc]
                                            timeout=30.0
                                        )
                                        normalized_result = _normalize_tool_result(result)
                                        await event_bus.emit_agent_tool_result(sid, task_tool, normalized_result)
                                        
                                        all_results.append({
                                            "tool": task_tool,
                                            "params": task_params,
                                            "result": normalized_result,
                                            "success": True
                                        })
                                        all_succeeded.append(task_tool)
                                        
                                    except asyncio.TimeoutError:
                                        error_msg = f"Task {task_tool} timed out"
                                        all_failed.append({
                                            "task": task_tool,
                                            "error": error_msg
                                        })
                                        await event_bus.emit_error(sid, req.message_id, error_msg)
                                    except Exception as task_err:
                                        error_msg = f"Task {task_tool} failed: {str(task_err)}"
                                        all_failed.append({
                                            "task": task_tool,
                                            "error": error_msg
                                        })
                                        await event_bus.emit_error(sid, req.message_id, error_msg)
                                        print(f"Multi-task execution error for {task_tool}: {error_msg}")
                                
                                # Generate comprehensive response from all results
                                await event_bus.emit_agent_status(sid, stage="planning", detail="Generating multi-task summary")
                                datetime_info = _get_current_datetime_info()
                                user_language_hint = _get_language_instruction()
                                
                                multi_task_summary_prompt = (
                                    f"You are an expert Hotel Operations Assistant. Generate a comprehensive summary of multiple tasks that were executed.\n\n"
                                    f"{user_language_hint}\n\n"
                                    f"{datetime_info}\n\n"
                                    f"TASKS EXECUTED:\n{json.dumps(all_results, indent=2, ensure_ascii=False)}\n\n"
                                    f"SUCCEEDED: {len(all_succeeded)} tasks\n"
                                    f"FAILED: {len(all_failed)} tasks\n\n"
                                    f"Generate a clear, user-friendly message that:\n"
                                    f"1. Lists all tasks that succeeded with relevant details\n"
                                    f"2. Mentions any tasks that failed with brief error information\n"
                                    f"3. Uses bullet points for clarity\n"
                                    f"4. Matches the user's language and tone\n\n"
                                    f"STRICT: Follow the language rule above EXACTLY. No exceptions."
                                )
                                
                                conversation_messages = _get_safe_messages(req.messages, limit=6)
                                summary_stream = llm_sarvam.chat_stream([
                                    {"role": "system", "content": multi_task_summary_prompt},
                                    *conversation_messages
                                ])
                                summary_acc = []
                                async for token in summary_stream:
                                    summary_acc.append(token)
                                    await event_bus.emit_token(sid, req.message_id or "", token)
                                text = "".join(summary_acc)
                                
                                # Final summarization
                                await event_bus.emit_agent_status(sid, stage="planning", detail="Finalizing response")
                                final_summary_prompt = (
                                    f"{SUMMARY_SYSTEM_PROMPT}\n\n"
                                    f"{datetime_info}\n\n"
                                    f"{user_language_hint}\n\n"
                                    "This is a multi-task execution summary. Be concise and clear."
                                )
                                final_stream = llm_sarvam.chat_stream([
                                    {"role": "system", "content": final_summary_prompt},
                                    {"role": "user", "content": text},
                                ], max_completion_tokens=settings.summary_max_tokens)
                                final_acc = []
                                async for token in final_stream:
                                    final_acc.append(token)
                                    await event_bus.emit_token(sid, req.message_id or "", token)
                                text = "".join(final_acc)
                except Exception as multi_task_err:
                    error_msg = f"Multi-task execution failed: {str(multi_task_err)}"
                    print(f"Multi-task error: {error_msg}")
                    await event_bus.emit_error(sid, req.message_id, error_msg)
                    text = f"Sorry, I couldn't complete the multi-task request: {str(multi_task_err)}"
            else:
                # SINGLE TASK HANDLING (existing logic)
                try:
                    # Build context from conversation history
                    context_messages = [m.model_dump() for m in req.messages[-10:]]
                    context_str = json.dumps(context_messages, ensure_ascii=False)
                    
                    # Initialize variables for tool execution
                    tool: Optional[str] = None
                    params: Dict[str, Any] = {}
                    status: bool = False
                    missing_params: Optional[Dict[str, Any]] = None
                    
                    # Check if this is a follow-up with pending state
                    if pending_state and pending_state.get("intent") == "task_write":
                        # This is a follow-up: re-run task_fetch with user's response
                        await event_bus.emit_agent_status(sid, stage="planning", detail=f"Processing follow-up for {tool_group}")
                        
                        try:
                            client_token = token_storage.get_token(sid) or settings.backend_api_token
                            task_result = await asyncio.wait_for(
                                _llm1_task_fetch(req.messages, tool_group, context=context_str, token=client_token),
                                timeout=20.0
                            )
                        except asyncio.TimeoutError:
                            error_msg = "Task parameter extraction timed out"
                            await event_bus.emit_error(sid, req.message_id, error_msg)
                            text = "Sorry, analyzing your response took too long. Please try again."
                        except Exception as task_fetch_err:
                            error_msg = f"Task parameter extraction failed: {str(task_fetch_err)}"
                            print(f"Task fetch error: {error_msg}")
                            await event_bus.emit_error(sid, req.message_id, error_msg)
                            text = f"Sorry, I couldn't understand your response: {str(task_fetch_err)}"
                        else:
                            tool = task_result.get("tool")
                            params = task_result.get("params") or {}
                            status = task_result.get("status", False)
                            missing_params = task_result.get("missing_params")

                            # Auto-detect priceModel for rates_set if missing
                            if tool == "rates_set" and params.get("roomTypeId") and not params.get("priceModel"):
                                try:
                                    client_token = token_storage.get_token(sid)
                                    room_data = await mcp_tools.room_type_get(params["roomTypeId"], token=client_token)
                                    if isinstance(room_data, dict) and room_data.get("priceModel"):
                                        detected_model = room_data["priceModel"]
                                        print(f"🤖 Auto-detected priceModel: {detected_model}")
                                        params["priceModel"] = detected_model
                                        
                                        if missing_params:
                                            missing_params.pop("priceModel", None)
                                            # Adjust required fields based on model
                                            if detected_model == "perPerson":
                                                missing_params.pop("baseRate", None)
                                                if not params.get("adultPrice"):
                                                    missing_params["adultPrice"] = "Adult price (required for per-person)"
                                            elif detected_model == "perRoom":
                                                missing_params.pop("adultPrice", None) 
                                                missing_params.pop("childPrice", None)
                                                if not params.get("baseRate"):
                                                    missing_params["baseRate"] = "Base rate (required for per-room)"
                                            
                                            # Check if we are done
                                            if not missing_params:
                                                status = True
                                except Exception as e:
                                    print(f"⚠️ Failed to auto-detect price model: {e}")
                            
                            # If still missing params, ask again
                            if not status and missing_params:
                                # Store state again for next follow-up
                                _set_conversation_state(sid, {
                                    "intent": "task_write",
                                    "tool_group": tool_group,
                                    "tool": tool,
                                    "params": params,
                                    "context": context_str
                                })
                                await event_bus.emit_agent_status(sid, stage="planning", detail="Requesting missing information")
                                query_text = await _llm1_handle_missing_params(
                                    req.messages,
                                    tool or "unknown",
                                    missing_params,
                                    context=context_str,
                                    partial_params=params,
                                    sid=sid
                                )
                                text = query_text
                            elif not tool:
                                error_msg = "Could not determine specific tool"
                                await event_bus.emit_error(sid, req.message_id, error_msg)
                                text = "I couldn't determine which specific task to perform. Please be more specific."
                            else:
                                # Params are complete, proceed to execution
                                # Fall through to execution code below
                                pass
                    else:
                        # Normal flow: first time processing this request
                        # Use task_fetch LLM to extract tool and params
                        await event_bus.emit_agent_status(sid, stage="planning", detail=f"Analyzing {tool_group} task request")
                        try:
                            client_token = token_storage.get_token(sid) or settings.backend_api_token
                            task_result = await asyncio.wait_for(
                                _llm1_task_fetch(req.messages, tool_group, context=context_str, token=client_token),
                                timeout=20.0
                            )
                        except asyncio.TimeoutError:
                            error_msg = "Task parameter extraction timed out"
                            await event_bus.emit_error(sid, req.message_id, error_msg)
                            text = "Sorry, analyzing your task request took too long. Please try again."
                        except Exception as task_fetch_err:
                            error_msg = f"Task parameter extraction failed: {str(task_fetch_err)}"
                            print(f"Task fetch error: {error_msg}")
                            await event_bus.emit_error(sid, req.message_id, error_msg)
                            text = f"Sorry, I couldn't understand your task request: {str(task_fetch_err)}"
                        else:
                            tool = task_result.get("tool")
                            params = task_result.get("params") or {}
                            status = task_result.get("status", False)
                            missing_params = task_result.get("missing_params")

                            # Auto-detect priceModel for rates_set if missing
                            if tool == "rates_set" and params.get("roomTypeId") and not params.get("priceModel"):
                                try:
                                    client_token = token_storage.get_token(sid)
                                    room_data = await mcp_tools.room_type_get(params["roomTypeId"], token=client_token)
                                    if isinstance(room_data, dict) and room_data.get("priceModel"):
                                        detected_model = room_data["priceModel"]
                                        print(f"🤖 Auto-detected priceModel: {detected_model}")
                                        params["priceModel"] = detected_model
                                        
                                        if missing_params:
                                            missing_params.pop("priceModel", None)
                                            # Adjust required fields based on model
                                            if detected_model == "perPerson":
                                                missing_params.pop("baseRate", None)
                                                if not params.get("adultPrice"):
                                                    missing_params["adultPrice"] = "Adult price (required for per-person)"
                                            elif detected_model == "perRoom":
                                                missing_params.pop("adultPrice", None) 
                                                missing_params.pop("childPrice", None)
                                                if not params.get("baseRate"):
                                                    missing_params["baseRate"] = "Base rate (required for per-room)"
                                            
                                            # Check if we are done
                                            if not missing_params:
                                                status = True
                                except Exception as e:
                                    print(f"⚠️ Failed to auto-detect price model: {e}")
                            
                            # Check if params are complete
                            if not status and missing_params:
                                # Store state for follow-up
                                _set_conversation_state(sid, {
                                    "intent": "task_write",
                                    "tool_group": tool_group,
                                    "tool": tool,
                                    "params": params,
                                    "context": context_str
                                })
                                # Handle missing params - ask user politely
                                await event_bus.emit_agent_status(sid, stage="planning", detail="Requesting missing information")
                                query_text = await _llm1_handle_missing_params(
                                    req.messages,
                                    tool or "unknown",
                                    missing_params,
                                    context=context_str,
                                    partial_params=params,
                                    sid=sid
                                )
                                text = query_text
                            elif not tool:
                                error_msg = "Could not determine specific tool"
                                await event_bus.emit_error(sid, req.message_id, error_msg)
                                text = "I couldn't determine which specific task to perform. Please be more specific."
                            else:
                                # Params are complete, proceed to execution
                                # Fall through to execution code below
                                pass
                    
                    # Execute task if we have complete params (from either normal flow or follow-up)
                    if tool and (status or (not missing_params)):
                        # Clear any pending state since we're executing
                        _clear_conversation_state(sid)
                        
                        # Special handling for reservation_create: calculate total amount, meal plan, status, etc.
                        if tool == "reservation_create" and isinstance(params.get("payload"), dict):
                            payload = params["payload"]
                            room_type_id = payload.get("roomType")
                            check_in_date = payload.get("checkInDate")
                            check_out_date = payload.get("checkOutDate")
                            
                            # Ensure numberOfRooms defaults to 1 (never null)
                            number_of_rooms = payload.get("numberOfRooms")
                            if number_of_rooms is None or number_of_rooms == 0:
                                number_of_rooms = 1
                            payload["numberOfRooms"] = number_of_rooms
                            
                            total_guest = payload.get("totalGuest")
                            meal_plan = payload.get("mealPlan")
                            
                            # Only calculate if we have all required fields
                            if room_type_id and check_in_date and check_out_date and total_guest and meal_plan:
                                try:
                                    client_token = token_storage.get_token(sid) or settings.backend_api_token
                                    
                                    # Fetch room type data for calculations
                                    room_type_data = await mcp_tools.room_type_get(room_type_id, token=client_token)
                                    if not room_type_data:
                                        raise ValueError(f"Room type {room_type_id} not found")
                                    
                                    price_model = room_type_data.get("priceModel", "perRoom")
                                    
                                    # Calculate total amount
                                    calculated_total = await _calculate_reservation_total(
                                        room_type_id=room_type_id,
                                        check_in_date=check_in_date,
                                        check_out_date=check_out_date,
                                        number_of_rooms=number_of_rooms,
                                        total_guest=total_guest,
                                        meal_plan=meal_plan,
                                        number_of_adults=payload.get("numberOfAdults"),
                                        number_of_children=payload.get("numberOfChildren"),
                                        token=client_token
                                    )
                                    
                                    # Always set totalAmount (never null)
                                    if calculated_total > 0:
                                        payload["totalAmount"] = calculated_total
                                    else:
                                        # Fallback: set to 0 if calculation fails
                                        payload["totalAmount"] = 0.0
                                    
                                    # Calculate meal plan details (matching Frontend logic)
                                    meal_plan_details = await _calculate_meal_plan_details(
                                        room_type_data=room_type_data,
                                        price_model=price_model,
                                        number_of_rooms=number_of_rooms,
                                        total_guest=total_guest,
                                        number_of_adults=payload.get("numberOfAdults"),
                                        number_of_children=payload.get("numberOfChildren"),
                                        meal_plan=meal_plan,
                                        check_in_date=check_in_date,
                                        check_out_date=check_out_date
                                    )
                                    payload.update(meal_plan_details)
                                    
                                    # Set status based on check-in date (matching Frontend logic)
                                    # If check-in is today → "checked-in", else → "confirmed"
                                    try:
                                        # Parse check-in date (might be ISO string with or without timezone)
                                        check_in_str = check_in_date.replace('Z', '+00:00') if 'Z' in check_in_date else check_in_date
                                        if 'T' not in check_in_str:
                                            # If only date (YYYY-MM-DD), add time
                                            check_in_str = f"{check_in_str}T00:00:00+00:00"
                                        check_in_dt = datetime.fromisoformat(check_in_str)
                                        
                                        # Get today's date in IST
                                        if USE_PYTZ and INDIA_TZ:
                                            today = datetime.now(INDIA_TZ)
                                        else:
                                            today = datetime.now(ZoneInfo("Asia/Kolkata"))
                                        
                                        # Convert check-in to IST for comparison (if it's in UTC)
                                        if check_in_dt.tzinfo is not None:
                                            # Convert to IST
                                            if USE_PYTZ and INDIA_TZ:
                                                check_in_ist = check_in_dt.astimezone(INDIA_TZ)
                                            else:
                                                check_in_ist = check_in_dt.astimezone(ZoneInfo("Asia/Kolkata"))
                                        else:
                                            # Assume it's already in IST or use naive comparison
                                            check_in_ist = check_in_dt
                                        
                                        # Compare dates (ignore time)
                                        if check_in_ist.date() == today.date():
                                            payload["status"] = "checked-in"
                                        else:
                                            payload["status"] = "confirmed"
                                    except Exception as date_err:
                                        print(f"Error determining status from check-in date: {date_err}")
                                        # Default to confirmed if date parsing fails
                                        payload["status"] = "confirmed"
                                    
                                    params["payload"] = payload
                                    await event_bus.emit_agent_think(sid, req.message_id or "", f"Calculated total amount: ₹{payload['totalAmount']:.2f}, Status: {payload['status']}")
                                except Exception as calc_err:
                                    print(f"Error calculating reservation details: {calc_err}")
                                    # Set defaults if calculation fails
                                    if "totalAmount" not in payload or payload.get("totalAmount") is None:
                                        payload["totalAmount"] = 0.0
                                    if "status" not in payload:
                                        payload["status"] = "confirmed"
                                    if "mealPlanAmount" not in payload:
                                        payload["mealPlanAmount"] = 0.0
                                    if "mealPlanGuestCount" not in payload:
                                        payload["mealPlanGuestCount"] = 0
                                    if "mealPlanRate" not in payload:
                                        payload["mealPlanRate"] = 0
                                    if "mealPlanNights" not in payload:
                                        payload["mealPlanNights"] = 0
                                    params["payload"] = payload
                        
                        # Get filtered tools for the group (write operations only)
                        filtered_tools = _get_tools_by_group(tool_group, operation="write")
                        
                        # Build tools JSON for agent (only the selected tool and its details)
                        tool_meta = _get_tool_metadata(tool)
                        tools_json = []
                        if tool_meta:
                            tool_info = {
                                "name": tool_meta["name"],
                                "description": tool_meta["description"],
                                "params": tool_meta["params"]
                            }
                            tools_json.append(tool_info)
                        else:
                            # Fallback: include all filtered tools
                            for t in filtered_tools:
                                tool_info = {
                                    "name": t["name"],
                                    "description": t["description"],
                                    "params": t["params"]
                                }
                                tools_json.append(tool_info)
                        
                        # Execute the tool directly
                        await event_bus.emit_agent_status(sid, stage="tool_call", detail=f"Executing {tool}")
                        fn = _resolve_tool(tool)
                        filtered_params = _filter_params(fn, params)
                        
                        # Special handling for room_update_status_by_number: normalize room number and status
                        if tool == "room_update_status_by_number" and isinstance(filtered_params.get("payload"), dict):
                            payload = filtered_params["payload"]
                            room_number = payload.get("roomNumber", "")
                            status = payload.get("status", "")
                            
                            # Normalize room number (remove "Room" prefix if present, keep just the number)
                            if room_number:
                                room_number = str(room_number).strip()
                                # Remove "Room" prefix if present
                                if room_number.lower().startswith("room"):
                                    room_number = room_number[4:].strip()
                                payload["roomNumber"] = room_number
                            
                            # If status is missing, try to infer from user's latest message
                            if not status and req.messages:
                                latest_message = req.messages[-1].content.lower()
                                # Check for blocking keywords
                                if any(keyword in latest_message for keyword in ["block", "blocked", "block room"]):
                                    status = "blocked"
                                elif any(keyword in latest_message for keyword in ["maintenance", "maintain", "repair"]):
                                    status = "maintenance"
                                elif any(keyword in latest_message for keyword in ["dirty", "clean"]):
                                    status = "dirty" if "dirty" in latest_message else "clean"
                                elif any(keyword in latest_message for keyword in ["out of order", "out-of-order", "ooo"]):
                                    status = "out-of-order"
                                elif any(keyword in latest_message for keyword in ["available", "vacant"]):
                                    status = "available"
                                elif any(keyword in latest_message for keyword in ["occupied"]):
                                    status = "occupied"
                            
                            # Map common blocking phrases to status values
                            if status:
                                status_lower = status.lower()
                                status_mapping = {
                                    "block": "blocked",
                                    "blocked": "blocked",
                                    "block room": "blocked",
                                    "maintenance": "maintenance",
                                    "maintain": "maintenance",
                                    "repair": "maintenance",
                                    "dirty": "dirty",
                                    "clean": "clean",
                                    "available": "available",
                                    "vacant": "available",
                                    "occupied": "occupied",
                                    "out of order": "out-of-order",
                                    "out-of-order": "out-of-order",
                                    "ooo": "out-of-order"
                                }
                                # Check if status matches any mapping
                                if status_lower in status_mapping:
                                    payload["status"] = status_mapping[status_lower]
                                # Also check if status contains blocking keywords
                                elif any(keyword in status_lower for keyword in ["block", "blocked"]):
                                    payload["status"] = "blocked"
                                elif any(keyword in status_lower for keyword in ["maintenance", "repair", "maintain"]):
                                    payload["status"] = "maintenance"
                                else:
                                    # Use the status as-is if it doesn't match any mapping
                                    payload["status"] = status
                        
                        # Special handling for rates_set: auto-determine priceModel if not provided
                        if tool == "rates_set" and isinstance(filtered_params.get("payload"), dict):
                            payload = filtered_params["payload"]
                            if "priceModel" not in payload or not payload.get("priceModel"):
                                # Try to determine from provided parameters
                                if "adultPrice" in payload or "childPrice" in payload:
                                    payload["priceModel"] = "perPerson"
                                elif "baseRate" in payload:
                                    payload["priceModel"] = "perRoom"
                                else:
                                    # Try to fetch from room type
                                    room_type_id = payload.get("roomTypeId")
                                    if room_type_id:
                                        try:
                                            client_token = token_storage.get_token(sid) or settings.backend_api_token
                                            room_type = await mcp_tools.room_type_get(room_type_id, token=client_token)
                                            if room_type and isinstance(room_type, dict):
                                                price_model = room_type.get("priceModel", "perRoom")
                                                payload["priceModel"] = price_model
                                        except Exception as e:
                                            print(f"Error fetching room type for priceModel: {e}")
                                            # Default to perRoom if we can't determine
                                            payload["priceModel"] = "perRoom"
                            
                            # Ensure dates is an array
                            if "dates" in payload and not isinstance(payload["dates"], list):
                                # Convert single date string to array
                                if isinstance(payload["dates"], str):
                                    payload["dates"] = [payload["dates"]]
                                else:
                                    payload["dates"] = []
                        
                        # Special handling for promotion_create: normalize fields to match backend schema
                        if tool == "promotion_create" and isinstance(filtered_params.get("payload"), dict):
                            payload = filtered_params["payload"]
                            
                            # Convert "endDate" to "lastdate" if user provided "endDate"
                            if "endDate" in payload and "lastdate" not in payload:
                                payload["lastdate"] = payload.pop("endDate")
                            
                            # Ensure discountType is set correctly based on user input
                            if "discount" in payload and "discountType" not in payload:
                                # Try to infer from user's latest message or discount value
                                discount_value = payload.get("discount")
                                if req.messages:
                                    latest_message = req.messages[-1].content.lower()
                                    # Check for percentage indicators
                                    if any(indicator in latest_message for indicator in ["%", "percent", "percentage", "discount"]):
                                        # If user said "50%" or "50 percent", it's percentage
                                        if "%" in latest_message or "percent" in latest_message:
                                            payload["discountType"] = "percentage"
                                        # If discount value is > 100, likely fixed amount
                                        elif isinstance(discount_value, (int, float)) and discount_value > 100:
                                            payload["discountType"] = "fixed"
                                        else:
                                            # Default to percentage for common discount values (1-100)
                                            payload["discountType"] = "percentage"
                                    else:
                                        # Default to percentage if no clear indicator
                                        payload["discountType"] = "percentage"
                                else:
                                    # Default to percentage if no message context
                                    payload["discountType"] = "percentage"
                            
                            # Normalize discountType to lowercase
                            if "discountType" in payload:
                                discount_type = str(payload["discountType"]).lower()
                                if discount_type in ["percentage", "percent", "%"]:
                                    payload["discountType"] = "percentage"
                                elif discount_type in ["fixed", "amount", "rupees", "rs"]:
                                    payload["discountType"] = "fixed"
                                else:
                                    # Default to percentage if unclear
                                    payload["discountType"] = "percentage"
                            
                            # Set isActive to true by default if not provided
                            if "isActive" not in payload:
                                payload["isActive"] = True
                        
                        # Get client token once for all special handling
                        client_token = token_storage.get_token(sid) or settings.backend_api_token
                        
                        # Special handling for check_availability: normalize dates and convert room type name to ID
                        if tool == "check_availability" and isinstance(filtered_params.get("payload"), dict):
                            payload = filtered_params["payload"]
                            
                            # Convert roomTypeIds (array) to roomTypeId (string) if present
                            if "roomTypeIds" in payload and isinstance(payload["roomTypeIds"], list) and len(payload["roomTypeIds"]) > 0:
                                payload["roomTypeId"] = payload.pop("roomTypeIds")[0]
                            
                            # Handle room type name to ID conversion
                            if "roomTypeId" in payload and payload["roomTypeId"]:
                                room_type_id = payload["roomTypeId"]
                                # If it's not a valid ObjectId format (24 hex chars), try to resolve it as a name
                                if not (isinstance(room_type_id, str) and len(room_type_id) == 24 and all(c in '0123456789abcdefABCDEF' for c in room_type_id)):
                                    try:
                                        resolved_id = await _resolve_name_to_id("roomType", room_type_id, client_token)
                                        if resolved_id:
                                            payload["roomTypeId"] = resolved_id
                                    except Exception as e:
                                        print(f"Error resolving room type name to ID: {e}")
                            
                            # Handle "today's availability" - if checkOutDate is missing or same as checkInDate, set to tomorrow
                            if "checkInDate" in payload and payload["checkInDate"]:
                                check_in = payload["checkInDate"]
                                check_out = payload.get("checkOutDate", "")
                                
                                # If check-out is missing or same as check-in, set to next day
                                if not check_out or check_out == check_in:
                                    try:
                                        check_in_date = datetime.fromisoformat(check_in.replace('Z', '+00:00').split('T')[0])
                                        check_out_date = check_in_date + timedelta(days=1)
                                        payload["checkOutDate"] = check_out_date.strftime('%Y-%m-%d')
                                    except Exception as e:
                                        print(f"Error calculating check-out date: {e}")
                                        # Fallback: use tomorrow's date string
                                        if check_in:
                                            try:
                                                check_in_obj = datetime.fromisoformat(check_in.split('T')[0])
                                                payload["checkOutDate"] = (check_in_obj + timedelta(days=1)).strftime('%Y-%m-%d')
                                            except:
                                                pass
                        
                        # Add client token if available
                        if client_token and "token" in inspect.signature(fn).parameters:
                            filtered_params["token"] = client_token
                        
                        await event_bus.emit_agent_tool_start(sid, tool, filtered_params)  # type: ignore[arg-type]
                        
                        try:
                            # Execute tool with timeout
                            result = await asyncio.wait_for(
                                fn(**filtered_params),  # type: ignore[misc]
                                timeout=30.0
                            )
                            normalized_result = _normalize_tool_result(result)
                            await event_bus.emit_agent_tool_result(sid, tool, normalized_result)
                            
                            # Check if tool execution actually succeeded
                            tool_succeeded = False
                            error_message = None
                            
                            # Check for error indicators in the result
                            if isinstance(normalized_result, dict):
                                # Check for error message field
                                if "error" in normalized_result:
                                    error_message = normalized_result.get("error")
                                elif "message" in normalized_result:
                                    msg = normalized_result.get("message", "")
                                    # Check if message indicates error
                                    if any(keyword in msg.lower() for keyword in ["error", "failed", "invalid", "missing", "not found", "unauthorized"]):
                                        error_message = msg
                                    # Check for success indicators
                                    elif any(keyword in msg.lower() for keyword in ["success", "updated", "created", "processed", "modified"]):
                                        tool_succeeded = True
                                # Check for success indicators in result structure
                                if not error_message:
                                    if "updatedRates" in normalized_result or "items" in normalized_result or "value" in normalized_result:
                                        tool_succeeded = True
                                    elif normalized_result.get("modifiedCount", 0) > 0 or normalized_result.get("upsertedCount", 0) > 0:
                                        tool_succeeded = True
                                    elif "message" in normalized_result and not error_message:
                                        # If there's a message but no error keywords, assume success
                                        tool_succeeded = True
                            
                            # If tool failed, show error instead of generating success message
                            if error_message:
                                await event_bus.emit_agent_status(sid, stage="planning", detail="Tool execution failed")
                                error_text = f"Sorry, I couldn't complete the task: {error_message}"
                                draft = error_text
                            elif not tool_succeeded:
                                # If we can't determine success, be cautious
                                await event_bus.emit_agent_status(sid, stage="planning", detail="Verifying tool result")
                                warning_text = "The task was executed, but I couldn't verify if it succeeded. Please check the system to confirm."
                                draft = warning_text
                            else:
                                # Tool succeeded - generate success message
                                await event_bus.emit_agent_status(sid, stage="planning", detail="Generating response")
                                group_info = TOOL_GROUPS.get(tool_group, {})
                                datetime_info = _get_current_datetime_info()
                                user_language_hint = _get_language_instruction()
                                agent_system_prompt = (
                                    f"You are an expert PMS operations agent specializing in {group_info.get('name', tool_group)} tasks.\n\n"
                                    f"{user_language_hint}\n\n"
                                    f"{datetime_info}\n\n"
                                    f"You just executed the tool '{tool}' with these parameters: {json.dumps(params, indent=2)}\n\n"
                                    f"The tool returned this result: {json.dumps(normalized_result, indent=2, ensure_ascii=False)}\n\n"
                                    f"Generate a clear, user-friendly message describing what was done and the result. "
                                    f"Be specific about what was created, updated, or modified. Include relevant details like IDs, amounts, dates, etc.\n\n"
                                    f"IMPORTANT: Only confirm success if the result clearly indicates success (e.g., 'updatedRates', 'message' with success keywords, modifiedCount > 0). "
                                    f"If the result is unclear or indicates failure, mention that verification is needed.\n\n"
                                    f"STRICT: Follow the language rule above EXACTLY. No exceptions."
                                )
                                
                                # Filter out system messages - API requires user/assistant only after system message
                                conversation_messages = _get_safe_messages(req.messages, limit=6)
                                
                                agent_stream = llm_sarvam.chat_stream([
                                    {"role": "system", "content": agent_system_prompt},
                                    *conversation_messages
                                ])
                                agent_acc = []
                                async for token in agent_stream:
                                    agent_acc.append(token)
                                    await event_bus.emit_token(sid, req.message_id or "", token)
                                draft = "".join(agent_acc)
                        except asyncio.TimeoutError:
                            error_msg = f"Tool {tool} timed out after 30 seconds"
                            await event_bus.emit_error(sid, req.message_id, error_msg)
                            draft = f"Sorry, executing {tool} took too long. Please try again."
                        except Exception as tool_err:
                            error_msg = f"Tool {tool} failed: {str(tool_err)}"
                            print(f"Tool error: {error_msg}")
                            await event_bus.emit_error(sid, req.message_id, error_msg)
                            draft = f"Sorry, I couldn't complete the task: {str(tool_err)}"

                        await event_bus.emit_agent_status(sid, stage="planning", detail="Summarizing")
                        # Stream summarization with task_write intent-aware prompt and context
                        datetime_info = _get_current_datetime_info()
                        user_language_hint = _get_language_instruction()
                        summary_prompt = (
                            f"{SUMMARY_SYSTEM_PROMPT}\n\n"
                            f"{datetime_info}\n\n"
                            f"{user_language_hint}\n\n"
                            "This is a task execution result. The user requested an action to be performed. "
                            "You can use appropriate action language to describe what was done.\n\n"
                            f"CONTEXT: {context_str}"
                        )
                        sum_stream = llm_sarvam.chat_stream([
                            {"role": "system", "content": summary_prompt},
                            {"role": "user", "content": draft},
                        ], max_completion_tokens=settings.summary_max_tokens)
                        sum_acc = []
                        async for token in sum_stream:
                            sum_acc.append(token)
                            await event_bus.emit_token(sid, req.message_id or "", token)
                        text = "".join(sum_acc)
                except Exception as task_err:
                    error_msg = f"Task execution failed: {str(task_err)}"
                    print(f"Task error: {error_msg}")
                    await event_bus.emit_error(sid, req.message_id, error_msg)
                    text = f"Sorry, I couldn't complete the task: {str(task_err)}"

        else:
            await event_bus.emit_agent_status(sid, stage="planning", detail="Generating response")
            datetime_info = _get_current_datetime_info()
            user_language_hint = _get_language_instruction()
            system_content = (
                f"Be helpful and concise.\n\n"
                f"{user_language_hint}\n\n"
                f"{datetime_info}\n\n"
                "STRICT: Follow the language rule above EXACTLY. No exceptions."
            )
            # Stream fallback response
            # Filter out system messages - API requires user/assistant only after system message
            conversation_messages = _get_safe_messages(req.messages, limit=10)
            
            stream = llm_sarvam.chat_stream([
                {"role": "system", "content": system_content},
                *conversation_messages,
            ])
            acc = []
            async for token in stream:
                acc.append(token)
                await event_bus.emit_token(sid, req.message_id or "", token)
            draft = "".join(acc)

            await event_bus.emit_agent_status(sid, stage="planning", detail="Summarizing")
            # Fallback summarization (general chat)
            datetime_info = _get_current_datetime_info()
            user_language_hint = _get_language_instruction()
            sum_stream = llm_sarvam.chat_stream([
                {"role": "system", "content": f"{SUMMARY_SYSTEM_PROMPT}\n\n{datetime_info}\n\n{user_language_hint}"},
                {"role": "user", "content": draft},
            ], max_completion_tokens=settings.summary_max_tokens)
            sum_acc = []
            async for token in sum_stream:
                sum_acc.append(token)
                await event_bus.emit_token(sid, req.message_id or "", token)
            text = "".join(sum_acc)

        await event_bus.emit_agent_status(sid, stage="done", detail="Response ready")
        await event_bus.emit_done(sid, req.message_id or "", text)
    except Exception as exc:
        error_msg = str(exc)
        print(f"Error in handle_socket_message: {error_msg}")  # Debug log
        await event_bus.emit_error(sid, data.get("message_id"), error_msg)
        # Always emit done even on error so frontend stops showing "thinking"
        await event_bus.emit_done(sid, data.get("message_id") or "", f"Sorry, an error occurred: {error_msg}")

