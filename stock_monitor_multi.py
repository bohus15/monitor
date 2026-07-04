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
from urllib.parse import urlparse
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

# Zachytava ceny typu "1 Kč", "12 990 Kč", "0,00 €", "799,96 €", "0,01 zł"
PRICE_PATTERN = re.compile(r"\d[\d\s.,]*\s?(?:Kč|€|zł)")

# Niektore e-shopy (napr. loficards.pl - Sky-Shop platforma) vykresluju cenu
# ako HOLE cislo bez meny v HTML/texte (mena sa dokresluje cez CSS/JS a teda
# nie je vidno v texte stranky). Pre take pripady pouzivame fallback: hladame
# RIADOK, ktory obsahuje LEN cislo s desatinnou ciarkou/bodkou (napr. "0.01",
# "199,99") a k nemu dopiseme menu podla domeny. Cele cisla bez desatiny
# (napr. "15", "18" - casto poplatky za dopravu) sa NEBERU, aby sme
# nezachytili nahodne ine hodnoty.
BARE_PRICE_PATTERN = re.compile(r"^\d+[.,]\d{1,2}$")

# Mena podla domeny/TLD - pouziva sa len pre fallback BARE_PRICE_PATTERN
CURRENCY_BY_DOMAIN = {
    "loficards.pl": "zł",
}
CURRENCY_BY_TLD = {
    ".cz": "Kč",
    ".sk": "€",
    ".pl": "zł",
}


def guess_currency(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    for domain, currency in CURRENCY_BY_DOMAIN.items():
        if domain in netloc:
            return currency
    for tld, currency in CURRENCY_BY_TLD.items():
        if netloc.endswith(tld):
            return currency
    return "€"

# CZ aj SK varianty stavovych textov, v poradi podla priority vyskytu
STATUS_KEYWORDS = [
    "Skladom",
    "Skladem",
    "Dostupné",
    "Dostępne",
    "Na otázku",
    "Na dotaz",
    "Predobjednávka",
    "Předobjednávka",
    "Przedsprzedaż",
    "Pripravujeme",
    "Připravujeme",
    "Nedostupné",
    "Vypredané",
    "Vyprodáno",
    "Brak towaru",
    "Cenu ešte nepoznáme",
    "Očakávame",
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


def get_page(url: str):
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    # separator "\n" zachova hranice riadkov/blokov, aby sme vedeli
    # oddelit "Dostupnost: X" od nasledujuceho odstavca/poznamky
    text = soup.get_text("\n", strip=True)
    return soup, text


def extract_prices(text: str, url: str = "") -> str:
    found = []
    for line in text.split("\n"):
        for m in PRICE_PATTERN.finditer(line):
            found.append(normalize_price(m.group()))

    if not found:
        # Fallback pre e-shopy, ktore vykresluju cenu ako hole cislo bez
        # meny v texte (mena je dokreslena len vizualne cez CSS/JS a v HTML
        # texte ju vobec nevidno - typicky napr. loficards.pl).
        currency = guess_currency(url)
        for line in text.split("\n"):
            line = line.strip()
            if BARE_PRICE_PATTERN.match(line):
                found.append(normalize_price(f"{line} {currency}"))

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
    "dostępność",
    "dostepnosc",
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


def extract_ihrysko(soup: BeautifulSoup):
    """
    Ihrysko.sk ma na stranke VELA textu, ktory nahodne obsahuje slovo
    "dostupnosť" (napr. vo vete "Vzhľadom na nižšiu dostupnosť tohto
    produktu je určený limit 1ks na zákazníka.") a tiez viacero cien
    naraz (cena produktu, cena dopravy, hranica pre dopravu zadarmo).
    Preto sa tu NEPOUZIVA generic text-scan cez celu stranku, ale sa
    ide priamo do konkretneho HTML bloku s cenou/dostupnostou
    (div.product-pricing), ktory je unikatny pre hlavny produkt.
    """
    price = "ZIADNA CENA NAJDENA"
    status = "NEZNAME"

    pricing_div = soup.select_one("div.product-pricing")
    if not pricing_div:
        return price, status

    price_container = pricing_div.select_one(".product-pricing__price")
    if price_container:
        ptext = price_container.get_text(" ", strip=True)
        m = PRICE_PATTERN.search(ptext)
        if m:
            price = normalize_price(m.group())
        elif ptext:
            # cena este nie je zverejnena (napr. "Cenu ešte nepoznáme")
            price = ptext

    avail_container = pricing_div.select_one(".product-pricing__availability")
    text_for_status = avail_container.get_text(" ", strip=True) if avail_container else ""
    text_for_status = text_for_status.replace("\ufeff", "").strip()

    if not text_for_status and price_container:
        # fallback: ak sa nenasiel samostatny availability blok, skus
        # dostupnost vycitat z toho isteho miesta ako cenu (niektore
        # stranky ("Cenu ešte nepoznáme" / "Očakávame") to majú spolu)
        text_for_status = price_container.get_text(" ", strip=True)

    if text_for_status:
        status = text_for_status

    return price, status


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
            soup, page_text = get_page(url)
        except requests.RequestException as e:
            print(f"[{name}] Chyba pri nacitavani stranky: {e}")
            continue

        domain = urlparse(url).netloc.lower()
        if "ihrysko.sk" in domain:
            current_price, current_status = extract_ihrysko(soup)
        else:
            current_price = extract_prices(page_text, url)
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
