"""Ortak HTTP ve önbellek dosyası yardımcıları. Ağ hatası ya da bozuk JSON None döner."""

from __future__ import annotations

import os

import requests

HEADERS = {"User-Agent": "CryptoHub-Desktop"}
TIMEOUT = 12


def get_json(url: str, params: dict | None = None):
    try:
        response = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException:
        return None
    try:
        return response.json()
    except ValueError:
        return None


def post_json(url: str, body: dict):
    try:
        response = requests.post(
            url,
            json=body,
            headers={**HEADERS, "Content-Type": "application/json"},
            timeout=TIMEOUT,
        )
        return response.json()
    except (requests.RequestException, ValueError):
        return None


def cache_path(name: str) -> str:
    """%APPDATA%\\CryptoHub altındaki dosya. Klasör yoksa açılır."""
    root = os.environ.get("APPDATA") or os.path.expanduser("~")
    folder = os.path.join(root, "CryptoHub")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, name)
