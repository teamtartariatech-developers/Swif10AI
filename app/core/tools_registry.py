from typing import Dict, Any, List, Optional
from ..mcp import tools as mcp_tools

TOOL_GROUPS = {
    "frontoffice": {
        "name": "Front Office & Reservations",
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
        "description": "Manages folios, bills, invoices, payments, and financial transactions. Used for adding charges, processing payments, checking out guests, and viewing folios.",
        "use_cases": [
            "Creating and managing folios",
            "Adding charges or payments",
            "Guest checkout and billing",
            "Viewing folio details"
        ]
    },
    "guest_management": {
        "name": "Guest Management",
        "description": "Manages guest profiles, history, preferences, reviews, and reputation. Used for creating/updating guest profiles, viewing guest history, and managing reviews.",
        "use_cases": [
            "Creating or updating guest profiles",
            "Viewing guest lists and details",
            "Managing guest reviews and feedback",
            "Viewing guest history"
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
        "description": "Lists reservations with optional filtering. Use this when the user asks to see reservations, bookings, or wants to filter by date, status, etc.",
        "params": {
            "params": {"type": "object", "required": False, "description": "Optional query parameters like date filters, status, etc."}
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
                    "mealPlan": {"type": "string", "required": True, "description": "Meal plan code: 'EP', 'CP', 'MAP', or 'AP'"},
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
        "description": "Lists all guest folios (bills). Use this when the user asks to see folios, bills, or guest accounts.",
        "params": {},
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
        "description": "Lists all guests. Use this when the user asks to see guests, guest list, or all guest profiles.",
        "params": {},
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
        "description": "Lists guest reviews. Use this when the user asks to see reviews, guest feedback, or reputation data.",
        "params": {},
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
        "description": "Lists all physical rooms. Use this when the user asks to see rooms, room list, or all available rooms.",
        "params": {},
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
        "description": "Lists all guest conversations. Use this when the user asks to see conversations, messages, or guest communications.",
        "params": {},
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

