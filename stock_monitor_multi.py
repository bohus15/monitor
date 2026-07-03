"""
Stock Monitor - KOMBINOVANE sledovanie CENY aj TEXTU dostupnosti
-----------------------------------------------------------------------
Sleduje obe veci naraz, aby sa neprepasla zmena, ak e-shop aktualizuje
len jednu z nich (napr. zmeni cenu, ale este chvilu neprepne text,
alebo naopak):

1) CENA - hlada vzory ako "1 Kč", "12 990 Kč", "0,00 €", "799,96 €"
2) TEXT STAVU - hlada slova ako "Skladom", "Skladem", "Nedostupné",
   "Připravujeme", "Predobjednávka" atd. (CZ aj SK varianty)

Ak sa zmeni CO I LEN JEDNO z toho oproti poslednemu behu, posle sa
Discord notifikacia, ktora uvedie, co presne sa zmenilo.

POZNAMKA: notifikacia sa posle aj pri UPLNE PRVOM behu pre kazdy
produkt (aby bolo hned vidiet, ze vsetko funguje).

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

# CZ aj SK varianty stavovych textov, v poradi podla priority vyskytu
STATUS_KEYWORDS = [
    "Skladom",
    "Skladem",
    "Dostupné",
    "Na otázku",
    "Na dotaz",
    "Predobjednávka",
    "Předobjednávka",
    "Pripravujeme",
    "Připravujeme",
    "Nedostupné",
    "Vypredané",
    "Vyprodáno",
]


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


def get_page_text(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    # separator "\n" zachova hranice riadkov/blokov, aby sme vedeli
    # oddelit "Dostupnost: X" od nasledujuceho odstavca/poznamky
    return soup.get_text("\n", strip=True)


def extract_prices(text: str) -> str:
    found = [normalize_price(m.group()) for m in PRICE_PATTERN.finditer(text)]
    seen = set()
    unique = []
    for price in found:
        if price not in seen:
            seen.add(price)
            unique.append(price)
    return " | ".join(unique) if unique else "ZIADNA CENA NAJDENA"


# Labely, za ktorymi realne nasleduje stav DANEHO produktu (nie odporucanych
# produktov ani inych casti stranky). Ak sa najde label, berie sa text hned
# za nim - to je presnejsie ako hladat kluc. slovo kdekolvek na cele stranke.
AVAILABILITY_LABELS = [
    "dostupnosť",
    "dostupnost",
    "skladová dostupnosť",
    "skladova dostupnost",
]


def extract_status(text: str) -> str:
    lines = text.split("\n")

    # 1) presnejsi sposob: najdi RIADOK obsahujuci label "Dostupnost:" a
    # zober iba zvysok TOHO ISTEHO riadku (nepreteka do dalsich odsekov)
    for line in lines:
        line_lower = line.lower()
        for label in AVAILABILITY_LABELS:
            idx = line_lower.find(label)
            if idx != -1:
                after = line[idx + len(label):].lstrip(" :\t").strip()
                if after:
                    return after
                # ak je label na konci riadku, hodnota moze byt na dalsom riadku
                # (niektore stranky renderuju label a hodnotu oddelene)

    # 1b) ak label najdeny na konci riadku bez hodnoty, skus najst hodnotu
    # v ramci nasledujuceho neprazdneho riadku
    for i, line in enumerate(lines):
        line_lower = line.lower()
        for label in AVAILABILITY_LABELS:
            if line_lower.rstrip().endswith(label) or line_lower.rstrip().endswith(label + ":"):
                for next_line in lines[i + 1:]:
                    if next_line.strip():
                        return next_line.strip()

    # 2) fallback: ak label nenajdeny nikde, hladaj kluc. slovo kdekolvek
    text_lower = text.lower()
    best_pos = None
    best_status = "NEZNAME"
    for keyword in STATUS_KEYWORDS:
        match = re.search(re.escape(keyword.lower()), text_lower)
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
            page_text = get_page_text(url)
        except requests.RequestException as e:
            print(f"[{name}] Chyba pri nacitavani stranky: {e}")
            continue

        current_price = extract_prices(page_text)
        current_status = extract_status(page_text)

        last = state.get(name, {})
        last_price = last.get("price")
        last_status = last.get("status")

        print(f"[{name}] Cena: {current_price} | Stav: {current_status}")
        print(f"[{name}] (predtym: {last_price} | {last_status})")

        price_changed = current_price != last_price
        status_changed = current_status != last_status

        if price_changed or status_changed:
            changes = []
            if price_changed:
                changes.append(f"💰 Cena: {last_price} → {current_price}")
            if status_changed:
                changes.append(f"📦 Stav: {last_status} → {current_status}")

            send_discord_notification(
                f"🟢 **{name}** - zmena!\n{url}\n" + "\n".join(changes)
            )

        state[name] = {"price": current_price, "status": current_status}

    save_last_state(state)


if __name__ == "__main__":
    main()
