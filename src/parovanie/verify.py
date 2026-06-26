"""Product-page extractor and code-based verdict helpers."""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from parovanie.models import Product
from parovanie.normalize import code_present

_WS = re.compile(r"\s+")


def extract_page(html: str) -> dict:
    """Extract title, code, and price from a product page HTML string.

    Supports both PrestaShop (wetland.sk) and Nette (huntingshop.eu) layouts.
    Returns a dict with keys "title", "code", "price" (all may be empty/None).
    """
    soup = BeautifulSoup(html, "lxml")

    # --- Title ---
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(strip=True)
    elif soup.title:
        # strip trailing site name (e.g. "Product | Site")
        raw = soup.title.get_text(strip=True)
        title = raw.split("|")[0].split(" - ")[0].strip() if "|" in raw or " - " in raw else raw
    else:
        title = ""

    # --- Code ---
    code: str | None = None

    # PrestaShop (wetland): <li class="detail"><div class="detail__left"><span>Kód</span></div>
    #                        <div class="detail__right"><span>3867-R57-M@</span></div></li>
    for label in soup.select(".detail__title"):
        if "kód" in label.get_text(strip=True).lower():
            row = label.find_parent("li") or label.find_parent("div", class_=re.compile(r"detail"))
            if row:
                right = row.select_one(".detail__right")
                if right:
                    code = right.get_text(strip=True) or None
            break

    # Nette (huntingshop): <span class="fs-5">Katalógové číslo: OT2699</span>
    if not code:
        for el in soup.select(".fs-5"):
            text = el.get_text(strip=True)
            m = re.search(r"(?:katalógové číslo|kód|sku)\s*[:\-]?\s*(.+)", text, re.I)
            if m:
                code = m.group(1).strip() or None
                break

    # Generic fallbacks
    if not code:
        for sel in ['[itemprop="sku"]', ".product-code", ".sku", ".kod", "[data-code]"]:
            el = soup.select_one(sel)
            if el:
                val = el.get("content") or el.get("data-code") or el.get_text(strip=True)
                if val and val.strip():
                    code = val.strip()
                    break

    # --- Price ---
    price: str | None = None

    # PrestaShop (wetland): .product__current-price
    for sel in [".product__current-price", ".product__discount"]:
        el = soup.select_one(sel)
        if el:
            val = el.get_text(strip=True)
            if val:
                price = val
                break

    # Nette (huntingshop): .product-price-on-sale inside product detail
    if not price:
        for sel in [
            ".product-detail .product-price-on-sale",
            ".product-detail .actual-price",
            ".price-vat",
        ]:
            el = soup.select_one(sel)
            if el:
                val = el.get_text(strip=True)
                if val:
                    price = val
                    break

    # Generic fallback
    if not price:
        for sel in ['[itemprop="price"]', ".price", ".product-price", ".cena"]:
            el = soup.select_one(sel)
            if el:
                val = (el.get("content") or el.get_text(strip=True) or "").strip()
                if val:
                    price = val
                    break

    return {
        "title": _WS.sub(" ", title or "").strip(),
        "code": code,
        "price": price,
    }


def code_verdict(product: Product, page: dict) -> tuple[str, str]:
    """Return ("OK", reason) if product.external_code appears in page title/code,
    else ("UNSURE", reason).
    """
    if not product.external_code:
        return "UNSURE", "no external code to verify against"

    hay = " ".join(filter(None, [page.get("title"), page.get("code")]))

    if code_present(product.external_code, hay):
        return "OK", f"code {product.external_code} present on page"
    return "UNSURE", f"code {product.external_code} not found on page"


def merge_verdict(report_rows: list[dict], verdicts: dict) -> list[dict]:
    """Fill verdict/verdict_reason/attempts columns in report rows.

    Args:
        report_rows: list of output row dicts (modified in place).
        verdicts: mapping of row index → dict with verdict/verdict_reason/attempts.

    Returns:
        The modified report_rows list.
    """
    for idx, v in verdicts.items():
        row = report_rows[idx]
        row["verdict"] = v.get("verdict", "")
        row["verdict_reason"] = v.get("verdict_reason", "")
        row["attempts"] = v.get("attempts", "")
    return report_rows
