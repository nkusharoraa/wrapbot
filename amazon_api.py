"""
Amazon SP-API integration module for WrapBot.
Handles LWA authentication and Orders API calls for India marketplace.
"""

import json
import time
from datetime import datetime, timezone, timedelta
import os
import sys
import requests


LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"


def _get_config_path():
    """Resolve the path to amazon_config.json relative to the executable or script."""
    if getattr(sys, 'frozen', False):
        app_path = os.path.dirname(sys.executable)
    else:
        app_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(app_path, 'amazon_config.json')


def load_config():
    """Load Amazon SP-API credentials from config file."""
    config_path = _get_config_path()
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Amazon config not found at {config_path}")
    with open(config_path, 'r') as f:
        return json.load(f)


def get_access_token(config=None):
    """Exchange refresh token for a short-lived LWA access token."""
    if config is None:
        config = load_config()

    payload = {
        "grant_type": "refresh_token",
        "refresh_token": config["refresh_token"],
        "client_id": config["client_id"],
        "client_secret": config["client_secret"],
    }

    resp = requests.post(LWA_TOKEN_URL, data=payload)
    resp.raise_for_status()
    data = resp.json()
    return data["access_token"]


def _sp_api_get(endpoint, path, access_token, params=None):
    """Make an authenticated GET request to the SP-API, retrying on 429."""
    url = f"{endpoint}{path}"
    headers = {
        "x-amz-access-token": access_token,
        "Content-Type": "application/json",
    }
    for attempt in range(5):
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        if resp.status_code == 429:
            wait = 2 ** attempt
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()


def get_unshipped_orders(config=None, access_token=None, statuses=None, days_window=90):
    """Fetch orders from India marketplace for the given statuses, handling pagination."""
    if config is None:
        config = load_config()
    if access_token is None:
        access_token = get_access_token(config)
    if statuses is None:
        statuses = ["Unshipped"]

    endpoint = config["endpoint"]
    marketplace_id = config["marketplace_id"]
    created_after = (datetime.now(timezone.utc) - timedelta(days=days_window)).strftime('%Y-%m-%dT%H:%M:%SZ')

    all_orders = []
    for status in statuses:
        params = {
            "MarketplaceIds": marketplace_id,
            "OrderStatuses": status,
            "CreatedAfter": created_after,
        }
        while True:
            data = _sp_api_get(endpoint, "/orders/v0/orders", access_token, params)
            payload = data.get("payload", {})
            orders = payload.get("Orders", [])
            all_orders.extend(orders)

            next_token = payload.get("NextToken")
            if not next_token:
                break
            params = {
                "MarketplaceIds": marketplace_id,
                "NextToken": next_token,
            }

    return all_orders


def get_order_items(order_id, config=None, access_token=None):
    """Fetch all items for a specific order, handling pagination."""
    if config is None:
        config = load_config()
    if access_token is None:
        access_token = get_access_token(config)

    endpoint = config["endpoint"]
    all_items = []
    params = {}

    while True:
        data = _sp_api_get(
            endpoint,
            f"/orders/v0/orders/{order_id}/orderItems",
            access_token,
            params if params else None,
        )
        payload = data.get("payload", {})
        items = payload.get("OrderItems", [])
        all_items.extend(items)

        next_token = payload.get("NextToken")
        if not next_token:
            break
        params = {"NextToken": next_token}

    return all_items


def fetch_all_pending_items(include_shipped=False):
    """
    Fetches Unshipped orders (and optionally Shipped) with their ship-by date.
    Returns a flat list of dicts: {title, qty, sku, order_id, asin, order_status, latest_ship_date}
    """
    config = load_config()
    access_token = get_access_token(config)

    orders = get_unshipped_orders(config, access_token, statuses=["Unshipped"])
    if include_shipped:
        shipped = get_unshipped_orders(config, access_token, statuses=["Shipped"], days_window=7)
        orders.extend(shipped)
    result = []

    for order in orders:
        order_id = order.get("AmazonOrderId", "")
        order_status = order.get("OrderStatus", "Unshipped")
        latest_ship_date = order.get("LatestShipDate", "")
        items = get_order_items(order_id, config, access_token)

        for item in items:
            result.append({
                "title": item.get("Title", ""),
                "qty": item.get("QuantityOrdered", 1),
                "sku": item.get("SellerSKU", ""),
                "order_id": order_id,
                "asin": item.get("ASIN", ""),
                "order_status": order_status,
                "latest_ship_date": latest_ship_date,
            })

    return result
