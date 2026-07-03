"""
Stock Monitor - sledovanie podla ZMENY CENY (nie textu dostupnosti)
-----------------------------------------------------------------------
Funguje bez ohladu na jazyk e-shopu (CZ/SK). Predobjednavkove produkty
maju typicky "placeholder" cenu (napr. "1 Kc" na Cardstore.cz alebo
"0,00 e" na Vesely-drak.sk), ktora sa zmeni na realnu cenu, ked sa
produkt da skutocne kupit / otvori sa predobjednavka.

Skript porovna, ci sa NAJDENE CENY na stranke zmenili oproti poslednej
kontrole. Ak ano, posle Discord notifikaciu (aj s hodnotami starej a
novej ceny).

POZNAMKA: notifikacia sa posle aj pri UPLNE PRVOM behu pre kazdy
produkt (aby bolo hned vidiet, ze vsetko funguje). Pri dalsich behoch
uz prichadza notifikacia len pri skutocnej zmene ceny.

Pozor: na strankach so ZOZNAMOM viacerych produktov (napr. filtrovany
vypis) skript sleduje VSETKY ceny na stranke naraz ako jeden celok.
Ak sa zmeni cena len JEDNEHO produktu zo zoznamu, dostanes notifikaciu,
ale skript ti nepovie presne KTORY produkt to bol - to uz treba
skontrolovat rucne na stranke.

products.json format:
[
  {"name": "Nazov", "url": "https://..."}
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

# Zachytava ceny typu "1 Kč", "12 990 Kč", "0,00 €", "799,96 €"
PRICE_PATTERN = re.compile(r"\d[\d\s.,]*\s?(?:Kč|€)")


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


def normalize_price(raw: str) -> str:
    return re.sub(r"\s+", " ", raw).strip()


def get_prices(url: str) -> list:
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    for tag in soup(["script", "style"]):
        tag.decompose()

    text = soup.get_text(" ", strip=True)
    found = [normalize_price(m.group()) for m in PRICE_PATTERN.finditer(text)]

    seen = set()
    unique = []
    for price in found:
        if price not in seen:
            seen.add(price)
            unique.append(price)
    return unique


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
            current_prices = get_prices(url)
        except requests.RequestException as e:
            print(f"[{name}] Chyba pri nacitavani stranky: {e}")
            continue

        current_key = " | ".join(current_prices) if current_prices else "ZIADNA CENA NAJDENA"
        last_key = state.get(name)

        print(f"[{name}] Aktualne ceny: {current_key}")
        print(f"[{name}] Posledne ceny:  {last_key}")

        if current_key != last_key:
            send_discord_notification(
                f"🟢 **{name}** - zmena ceny/dostupnosti!\n"
                f"{url}\n"
                f"Predtym: {last_key}\n"
                f"Teraz: {current_key}"
            )

        state[name] = current_key

    save_last_state(state)


if __name__ == "__main__":
    main()
