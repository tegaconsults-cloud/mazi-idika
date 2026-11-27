"""Image and document reading service.

Handles:
- Product images / photos of stock shelves → extract product names + prices
- Photos of handwritten stock books → OCR and structure
- PDF price lists → extract product catalogue
- Excel/CSV attachment hints (described in image caption)

Uses Gemini Vision (primary) or OpenAI GPT-4o vision (fallback).
Returns structured text that is then passed to the intent classifier.
"""

import base64
import logging
from typing import Optional

logger = logging.getLogger(__name__)


VISION_PROMPT = """You are helping a Nigerian market trader update their business inventory.

Analyse this image and extract any product information visible:
- Product names (use common Nigerian market names where recognisable)
- Quantities (numbers, cartons, bags, crates, tins, etc.)
- Prices (cost price and/or selling price in Naira)
- Any handwritten stock entries

Return a plain-text summary like:
"Indomie Chicken 70g — 20 cartons — cost ₦2,800 — sell ₦3,500
Peak Milk 170g — 10 tins — ₦950 cost"

If the image contains no product information, say "No product data found."
Keep the response concise. Do not include prices you are not confident about.
"""


async def extract_from_image(image_bytes: bytes, content_type: str = "image/jpeg") -> Optional[str]:
    """
    Extract product information from an image using Gemini Vision.
    Falls back to GPT-4o Vision if Gemini unavailable.
    Returns structured text or None.
    """
    text = await _gemini_vision(image_bytes, content_type)
    if text:
        return text
    return await _openai_vision(image_bytes, content_type)


async def _gemini_vision(image_bytes: bytes, content_type: str) -> Optional[str]:
    """Use Gemini 1.5 Flash vision."""
    try:
        import google.generativeai as genai
        from app.core.config import settings

        if not settings.GEMINI_API_KEY:
            return None

        genai.configure(api_key=settings.GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-1.5-flash")

        image_part = {
            "mime_type": content_type,
            "data": base64.b64encode(image_bytes).decode(),
        }
        response = model.generate_content([VISION_PROMPT, image_part])
        result = response.text.strip()
        return result if result and "No product data" not in result else None
    except Exception as exc:
        logger.warning(f"Gemini vision failed: {exc}")
        return None


async def _openai_vision(image_bytes: bytes, content_type: str) -> Optional[str]:
    """Use GPT-4o vision as fallback."""
    try:
        import openai
        from app.core.config import settings

        if not settings.OPENAI_API_KEY:
            return None

        b64 = base64.b64encode(image_bytes).decode()
        client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text",      "text": VISION_PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:{content_type};base64,{b64}"}},
                ],
            }],
            max_tokens=600,
        )
        result = resp.choices[0].message.content.strip()
        return result if result and "No product data" not in result else None
    except Exception as exc:
        logger.warning(f"OpenAI vision failed: {exc}")
        return None


async def extract_from_pdf(pdf_bytes: bytes) -> Optional[str]:
    """
    Extract text from a PDF price list or stock document.
    Uses basic pdfplumber extraction — no OCR needed for digital PDFs.
    Returns raw text, or None on failure.
    """
    try:
        import pdfplumber
        import io

        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages_text = []
            for page in pdf.pages[:5]:  # limit to first 5 pages
                text = page.extract_text()
                if text:
                    pages_text.append(text)
            return "\n".join(pages_text) or None
    except ImportError:
        logger.info("pdfplumber not installed — PDF extraction unavailable.")
        return None
    except Exception as exc:
        logger.warning(f"PDF extraction failed: {exc}")
        return None
