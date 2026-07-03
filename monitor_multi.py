"""
Stock Monitor - MULTI-produkt, bez potreby CSS selektora
------------------------------------------------------------
Namiesto CSS selektora skript hlada priamo standardne texty dostupnosti,
ktore pouziva platforma Shoptet (Cardstore.cz a mnoho dalsich CZ/SK
e-shopov na nej bezi): "Skladem", "Predobjednavka", "Pripravujeme",
"Vyprodano", "Na dotaz".

Vyhoda: netreba hladat CSS triedu cez F12 pre kazdy produkt zvlast.
Nevyhoda: ak e-shop nepouziva presne tieto slova, treba STATUS_KEYWORDS
upravit (str_replace/pripadne mi posli screenshot stranky a upravim to).

products.json format:
[
  {"name": "Nazov produktu", "url": "https://..."}
]
"""

import os
import re
import json
import requests
from bs4 import BeautifulSoup

DISCORD_WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
DISCORD_USER_ID = os.environ.get("DISCORD_USER_ID", "")

PRODUCTS_FILE = "products.json"
STATE_FILE = "last_state.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    )
}

# Poradie dolezite - ak sa na stranke najde viac zhod, berie sa ta,
# ktora sa v texte vyskytne NAJSKOR (typicky hned pri nazve produktu/cene).
# Case-sensitive naschval, aby sa to nepomylilo s VELKYMI PISMENAMI v menu
# (napr. "PŘEDOBJEDNÁVKY" v navigacii vs. "Předobjednávka" pri produkte).
STATUS_KEYWORDS = [
    "Skladem",
    "Předobjednávka",
    "Připravujeme",
    "Vyprodáno",
    "Na dotaz",
]

# Stavy, pri ktorych sa da produkt realne kupit / predobjednat
BUYABLE_STATUSES = {"Skladem", "Předobjednávka"}


def load_products() -> list:
    with open(PRODUCTS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_last_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_last_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def get_stock_status(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # odstranime skripty/styly aby sa nechytali falosne zhody
    for tag in soup(["script", "style"]):
        tag.decompose()

    text = soup.get_text(" ", strip=True)

    best_pos = None
    best_status = "NEZNAME"
    for keyword in STATUS_KEYWORDS:
        match = re.search(re.escape(keyword), text)
        if match and (best_pos is None or match.start() < best_pos):
            best_pos = match.start()
            best_status = keyword

    return best_status


def send_discord_notification(message: str) -> None:
    content = message
    if DISCORD_USER_ID:
        content = f"<@{DISCORD_USER_ID}> {message}"
    resp = requests.post(DISCORD_WEBHOOK_URL, json={"content": content}, timeout=10)
    if resp.status_code >= 300:
        print(f"Chyba pri odosielani do Discordu: {resp.status_code} {resp.text}")


def main() -> None:
    products = load_products()
    state = load_last_state()

    for product in products:
        name = product["name"]
        url = product["url"]

        try:
            current_status = get_stock_status(url)
        except requests.RequestException as e:
            print(f"[{name}] Chyba pri nacitavani stranky: {e}")
            continue

        last_status = state.get(name)
        print(f"[{name}] Aktualny stav: {current_status} | Posledny: {last_status}")

        if current_status != last_status:
            if current_status in BUYABLE_STATUSES:
                send_discord_notification(
                    f"🟢 **{name}** - zmena na: {current_status}!\n{url}"
                )
            else:
                send_discord_notification(
                    f"🔴 **{name}** - zmena stavu na: {current_status}.\n{url}"
                )
            state[name] = current_status

    save_last_state(state)


if __name__ == "__main__":
    main()
