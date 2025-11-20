from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Optional

import httpx
from ..config import settings

BACKEND_BASE_URL = settings.backend_base_url


def _auth_headers(token: Optional[str]) -> Dict[str, str]:
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    bearer = token or settings.backend_api_token
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    return headers


async def _request(
    method: str,
    path: str,
    *,
    json: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
    token: Optional[str] = None,
) -> Any:
    url = f"{BACKEND_BASE_URL}{path}"
    # Simple retry with backoff for transient errors
    attempt = 0
    last_exc: Optional[Exception] = None
    while attempt < 3:
        try:
            timeout = httpx.Timeout(30.0, connect=10.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.request(
                    method, url, headers=_auth_headers(token), json=json, params=params
                )
                # Check status code manually to provide better error context
                if resp.is_error:
                    error_detail = resp.text
                    try:
                        # Try to parse JSON error
                        error_json = resp.json()
                        if "message" in error_json:
                            error_detail = error_json["message"]
                        elif "error" in error_json:
                            error_detail = error_json["error"]
                    except:
                        pass
                    
                    # Return error object instead of raising exception (unless 401 which is critical)
                    if resp.status_code == 401:
                        raise RuntimeError(
                            f"Authentication failed (401): {error_detail}. "
                            f"Please set BACKEND_API_TOKEN environment variable with a valid backend API token."
                        )
                    
                    return {
                        "error": f"Backend request failed with status {resp.status_code}",
                        "message": error_detail,
                        "status_code": resp.status_code
                    }
                
                if resp.headers.get("content-type", "").startswith("application/json"):
                    return resp.json()
                return resp.text
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
            last_exc = exc
            attempt += 1
            await asyncio.sleep(0.3 * attempt)
    if last_exc:
        raise last_exc
    raise RuntimeError("Unknown MCP request failure")


# Reviews (Guest Management - Reputation)
async def reviews_list_all(token: Optional[str] = None) -> Any:
    return await _request("GET", "/api/guestmanagement/reputation/all", token=token)


async def reviews_list(params: Optional[Dict[str, Any]] = None, token: Optional[str] = None) -> Any:
    """
    List reviews with pagination.
    By default, limits to 15 items per page.
    """
    default_params = {"page": "1", "limit": "15"}
    if params:
        default_params.update(params)
    return await _request("GET", "/api/guestmanagement/reputation", params=default_params, token=token)


async def review_get(review_id: str, token: Optional[str] = None) -> Any:
    return await _request("GET", f"/api/guestmanagement/reputation/{review_id}", token=token)


async def review_create(payload: Dict[str, Any], token: Optional[str] = None) -> Any:
    return await _request("POST", "/api/guestmanagement/reputation", json=payload, token=token)


async def review_update(review_id: str, payload: Dict[str, Any], token: Optional[str] = None) -> Any:
    return await _request("PUT", f"/api/guestmanagement/reputation/{review_id}", json=payload, token=token)


async def review_delete(review_id: str, token: Optional[str] = None) -> Any:
    return await _request("DELETE", f"/api/guestmanagement/reputation/{review_id}", token=token)


async def reviews_summarize(payload: Dict[str, Any], token: Optional[str] = None) -> Any:
    # Expected payload typically: { reviews: string[] , language?: string }
    return await _request("POST", "/api/guestmanagement/reputation/summarize", json=payload, token=token)


# Guests (Guest Management - Guests)
async def guests_list(params: Optional[Dict[str, Any]] = None, token: Optional[str] = None) -> Any:
    """
    List guests with pagination.
    By default, limits to 15 items per page.
    """
    default_params = {"page": "1", "limit": "15"}
    if params:
        default_params.update(params)
    return await _request("GET", "/api/guestmanagement/guests", params=default_params, token=token)


async def guest_create_or_update(payload: Dict[str, Any], token: Optional[str] = None) -> Any:
    # Creates a new guest if not exists, otherwise updates based on matching keys (e.g., email/phone)
    return await _request("POST", "/api/guestmanagement/create-or-update", json=payload, token=token)


async def guest_update(guest_id: str, payload: Dict[str, Any], token: Optional[str] = None) -> Any:
    return await _request("PUT", f"/api/guestmanagement/guest/{guest_id}", json=payload, token=token)


async def guest_profile_with_reservation(payload: Dict[str, Any], token: Optional[str] = None) -> Any:
    # Creates a guest profile and an initial reservation together
    return await _request("POST", "/api/guestmanagement", json=payload, token=token)


async def guest_add_to_reservation(reservation_id: str, payload: Dict[str, Any], token: Optional[str] = None) -> Any:
    # Adds a guest entry to an existing reservation
    return await _request("POST", f"/api/guestmanagement/reservation/{reservation_id}", json=payload, token=token)


# Reservations (Front Office)
async def reservation_create(payload: Dict[str, Any], token: Optional[str] = None) -> Any:
    return await _request("POST", "/api/frontoffice/reservations", json=payload, token=token)


async def reservation_get(reservation_id: str, token: Optional[str] = None) -> Any:
    return await _request("GET", f"/api/frontoffice/reservations/{reservation_id}", token=token)


async def reservations_list(params: Optional[Dict[str, Any]] = None, token: Optional[str] = None) -> Any:
    """
    List reservations with pagination and filters.
    By default, limits to 15 items per page.
    """
    default_params = {"page": "1", "limit": "15"}
    if params:
        default_params.update(params)
    return await _request("GET", "/api/frontoffice/reservations", params=default_params, token=token)


async def reservations_list_all(token: Optional[str] = None) -> Any:
    return await _request("GET", "/api/frontoffice/reservations/all", token=token)


async def reservations_departures(date_iso: str, token: Optional[str] = None) -> Any:
    return await _request("GET", f"/api/frontoffice/reservations/departures/{date_iso}", token=token)


async def reservations_arrivals(date_iso: str, token: Optional[str] = None) -> Any:
    return await _request("GET", f"/api/frontoffice/reservations/arrivals/{date_iso}", token=token)


async def reservation_update(reservation_id: str, payload: Dict[str, Any], token: Optional[str] = None) -> Any:
    return await _request("PUT", f"/api/frontoffice/reservations/{reservation_id}", json=payload, token=token)


async def reservation_delete(reservation_id: str, token: Optional[str] = None) -> Any:
    return await _request("DELETE", f"/api/frontoffice/reservations/{reservation_id}", token=token)


# Foundation / Availability
async def check_availability(payload: Dict[str, Any], token: Optional[str] = None) -> Any:
    # Expected payload typically includes date range and room type(s)
    return await _request("POST", "/api/foundation/check-availability", json=payload, token=token)


# Communication (Campaigns, Conversations) - basic helpers
async def conversations_list(params: Optional[Dict[str, Any]] = None, token: Optional[str] = None) -> Any:
    """
    List conversations with pagination.
    By default, limits to 20 items per page.
    """
    default_params = {"page": "1", "limit": "20"}
    if params:
        default_params.update(params)
    return await _request("GET", "/api/guestmanagement/communication/conversations", params=default_params, token=token)


async def conversation_get(conversation_id: str, token: Optional[str] = None) -> Any:
    return await _request("GET", f"/api/guestmanagement/communication/conversations/{conversation_id}", token=token)


async def campaign_create(payload: Dict[str, Any], token: Optional[str] = None) -> Any:
    return await _request("POST", "/api/guestmanagement/communication/campaigns", json=payload, token=token)

# Conversations messages
async def conversation_messages(conversation_id: str, token: Optional[str] = None) -> Any:
    return await _request("GET", f"/api/guestmanagement/communication/conversations/{conversation_id}/messages", token=token)

async def conversation_add_message(conversation_id: str, payload: Dict[str, Any], token: Optional[str] = None) -> Any:
    return await _request("POST", f"/api/guestmanagement/communication/conversations/{conversation_id}/messages", json=payload, token=token)

async def conversation_mark_read(conversation_id: str, payload: Dict[str, Any], token: Optional[str] = None) -> Any:
    return await _request("PUT", f"/api/guestmanagement/communication/conversations/{conversation_id}/messages/read", json=payload, token=token)


# Promotions (Distribution)
async def promotion_create(payload: Dict[str, Any], token: Optional[str] = None) -> Any:
    return await _request("POST", "/api/distribution/promotion/createPromotion", json=payload, token=token)

async def promotion_list(token: Optional[str] = None) -> Any:
    return await _request("GET", "/api/distribution/promotion/getPromotions", token=token)

async def promotion_update(promo_id: str, payload: Dict[str, Any], token: Optional[str] = None) -> Any:
    return await _request("PUT", f"/api/distribution/promotion/updatePromotion/{promo_id}", json=payload, token=token)

async def promotion_delete(promo_id: str, token: Optional[str] = None) -> Any:
    return await _request("DELETE", f"/api/distribution/promotion/deletePromotion/{promo_id}", token=token)


# Rates (Distribution)
async def rates_set(payload: Dict[str, Any], token: Optional[str] = None) -> Any:
    return await _request("POST", "/api/distribution/ratemanager/setRates", json=payload, token=token)

async def rates_get(params: Optional[Dict[str, Any]] = None, token: Optional[str] = None) -> Any:
    return await _request("GET", "/api/distribution/ratemanager/getRates", params=params, token=token)


# Inventory (Distribution)
async def inventory_monthly(params: Optional[Dict[str, Any]] = None, token: Optional[str] = None) -> Any:
    return await _request("GET", "/api/distribution/inventorymanager/monthly", params=params, token=token)

async def inventory_availability(params: Optional[Dict[str, Any]] = None, token: Optional[str] = None) -> Any:
    return await _request("GET", "/api/distribution/inventorymanager/availability", params=params, token=token)

async def inventory_room_types_availability(params: Optional[Dict[str, Any]] = None, token: Optional[str] = None) -> Any:
    return await _request("GET", "/api/distribution/inventorymanager/room-types-availability", params=params, token=token)

async def inventory_block(payload: Dict[str, Any], token: Optional[str] = None) -> Any:
    return await _request("POST", "/api/distribution/inventorymanager/block-inventory", json=payload, token=token)

async def inventory_blocks_list(token: Optional[str] = None) -> Any:
    return await _request("GET", "/api/distribution/inventorymanager/inventory-blocks", token=token)

async def inventory_unblock(payload: Dict[str, Any], token: Optional[str] = None) -> Any:
    return await _request("DELETE", "/api/distribution/inventorymanager/unblock-inventory", json=payload, token=token)


# Rooms / Room Types (Foundation)
async def rooms_list(params: Optional[Dict[str, Any]] = None, token: Optional[str] = None) -> Any:
    """
    List all physical rooms.
    Note: This endpoint returns all rooms without pagination by default.
    """
    return await _request("GET", "/api/foundation/getRooms", params=params, token=token)

async def room_get(room_id: str, token: Optional[str] = None) -> Any:
    return await _request("GET", f"/api/foundation/getRoom/{room_id}", token=token)

async def room_types_list(token: Optional[str] = None) -> Any:
    return await _request("GET", "/api/foundation/getRoomTypes", token=token)


async def room_type_get(room_type_id: str, token: Optional[str] = None) -> Any:
    return await _request("GET", f"/api/foundation/getRoomType/{room_type_id}", token=token)


async def room_update_status(room_id: str, payload: Dict[str, Any], token: Optional[str] = None) -> Any:
    """Update room status by room ID."""
    return await _request("PUT", f"/api/foundation/updateRoom/{room_id}", json=payload, token=token)


async def room_update_status_by_number(payload: Dict[str, Any], token: Optional[str] = None) -> Any:
    """Update room status by room number. Payload should include 'roomNumber' and 'status'."""
    return await _request("PUT", "/api/foundation/updateRoomStatus", json=payload, token=token)


# Folios / Billing-Finance
async def folios_list(params: Optional[Dict[str, Any]] = None, token: Optional[str] = None) -> Any:
    """
    List folios/bills with pagination.
    By default, limits to 15 items per page.
    """
    default_params = {"page": "1", "limit": "15"}
    if params:
        default_params.update(params)
    return await _request("GET", "/api/billingfinance/folios", params=default_params, token=token)

async def folio_get(folio_id: str, token: Optional[str] = None) -> Any:
    return await _request("GET", f"/api/billingfinance/folios/{folio_id}", token=token)

async def folio_create(payload: Dict[str, Any], token: Optional[str] = None) -> Any:
    return await _request("POST", "/api/billingfinance/folios", json=payload, token=token)

async def folio_add_charge(folio_id: str, payload: Dict[str, Any], token: Optional[str] = None) -> Any:
    return await _request("POST", f"/api/billingfinance/folios/{folio_id}/charges", json=payload, token=token)

async def folio_add_payment(folio_id: str, payload: Dict[str, Any], token: Optional[str] = None) -> Any:
    return await _request("POST", f"/api/billingfinance/folios/{folio_id}/payments", json=payload, token=token)

async def folio_update(folio_id: str, payload: Dict[str, Any], token: Optional[str] = None) -> Any:
    return await _request("PUT", f"/api/billingfinance/folios/{folio_id}", json=payload, token=token)

async def folio_checkout(folio_id: str, token: Optional[str] = None) -> Any:
    return await _request("POST", f"/api/billingfinance/folios/{folio_id}/checkout", json={}, token=token)

# Simple health
async def backend_health(token: Optional[str] = None) -> Any:
    # If there's no explicit health route, we can hit one lightweight route as a proxy for health
    try:
        return await settings_get_ai(token=token)
    except Exception:
        return {"ok": False}


# Settings (AI config storage in backend)
async def settings_get_ai(token: Optional[str] = None) -> Any:
    return await _request("GET", "/api/settings/ai", token=token)


async def settings_update_ai(payload: Dict[str, Any], token: Optional[str] = None) -> Any:
    return await _request("PUT", "/api/settings/ai", json=payload, token=token)


