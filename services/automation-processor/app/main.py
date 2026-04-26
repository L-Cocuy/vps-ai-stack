import base64
import hashlib
import os
import re
import threading
import uuid
from datetime import datetime
from io import BytesIO
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel
from pypdf import PdfReader
import pytesseract

SCHEMA_VERSION = "2026-04-12.receipt.v1"
CONFIDENCE_REVIEW_THRESHOLD = 0.75
DEFAULT_CURRENCY = os.getenv("DEFAULT_CURRENCY", "EUR")
DEFAULT_LANGUAGES = os.getenv("OCR_LANGUAGES", "eng+deu")
PDF_DIRECT_TEXT_MIN_CHARS = int(os.getenv("OCR_PDF_DIRECT_TEXT_MIN_CHARS", "80"))

_SIGNATURE_TO_RECORD: dict[str, str] = {}
_DEDUPE_LOCK = threading.Lock()

app = FastAPI(title="automation-processor", version="0.1.0")


class FilePayload(BaseModel):
    filename: str
    mime_type: str | None = None
    content_base64: str


class SourcePayload(BaseModel):
    channel: str
    source_message_id: str | None = None
    sender: str | None = None
    source_user: str | None = None


class StoragePayload(BaseModel):
    drive_file_id: str | None = None
    drive_web_url: str | None = None


class HintsPayload(BaseModel):
    language_hint: list[str] | None = None
    currency_hint: str | None = None
    timezone: str | None = None


class ReceiptExtractRequest(BaseModel):
    document_id: str
    file: FilePayload
    source: SourcePayload
    storage: StoragePayload | None = None
    hints: HintsPayload | None = None


def _parse_decimal(value: str) -> float | None:
    cleaned = re.sub(r"[^\d,.\-]", "", value.strip())
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "")
            cleaned = cleaned.replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(".", "")
        cleaned = cleaned.replace(",", ".")
    try:
        return round(float(cleaned), 2)
    except ValueError:
        return None


def _find_date(raw_text: str) -> str | None:
    iso_match = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", raw_text)
    if iso_match:
        return f"{iso_match.group(1)}-{iso_match.group(2)}-{iso_match.group(3)}"

    dotted = re.search(r"\b(\d{2})[./-](\d{2})[./-](20\d{2})\b", raw_text)
    if dotted:
        day, month, year = dotted.groups()
        return f"{year}-{month}-{day}"
    return None


def _money_candidates(raw_text: str) -> list[tuple[str, float]]:
    candidates: list[tuple[str, float]] = []
    patterns = [
        re.compile(r"\b(EUR|USD|GBP|CHF)\b[^\d]{0,8}([0-9][0-9.,]*)", re.IGNORECASE),
        re.compile(r"([0-9][0-9.,]*)[^\w]{0,3}\b(EUR|USD|GBP|CHF)\b", re.IGNORECASE),
    ]
    for pattern in patterns:
        for match in pattern.finditer(raw_text):
            left, right = match.group(1), match.group(2)
            if re.match(r"^[A-Za-z]{3}$", left):
                currency = left.upper()
                number = right
            else:
                currency = right.upper()
                number = left
            parsed = _parse_decimal(number)
            if parsed is not None:
                candidates.append((currency, parsed))
    return candidates


def _extract_payment_method(raw_text: str) -> str | None:
    lowered = raw_text.lower()
    if any(token in lowered for token in ("karte", "card", "visa", "mastercard", "debit", "credit")):
        return "card"
    if any(token in lowered for token in ("cash", "bar", "bargeld")):
        return "cash"
    if any(token in lowered for token in ("transfer", "bank", "uberweisung", "überweisung")):
        return "bank_transfer"
    return None


def _extract_merchant(raw_text: str) -> str | None:
    skip = {
        "datum",
        "date",
        "summe",
        "total",
        "mwst",
        "vat",
        "tax",
        "zahlung",
        "payment",
        "blurred",
        "cropped",
        "paper slip",
        "receipt",
    }
    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue
        lowered = line.lower()
        if any(token in lowered for token in skip):
            continue
        if re.fullmatch(r"[A-Z]{3}", line):
            continue
        if re.search(r"[a-zA-Z]", line):
            return line[:120]
    return None


def _extract_tax(raw_text: str) -> float | None:
    for line in raw_text.splitlines():
        lowered = line.lower()
        if any(token in lowered for token in ("mwst", "vat", "tax")):
            nums = re.findall(r"([0-9][0-9.,]*)", line)
            if nums:
                parsed = _parse_decimal(nums[-1])
                if parsed is not None and parsed > 0:
                    return parsed
    return None


def _classify(raw_text: str, merchant: str | None) -> tuple[str, str]:
    joined = f"{merchant or ''}\n{raw_text}".lower()
    if any(t in joined for t in ("billa", "spar", "lidl", "supermarket", "grocery")):
        return "groceries", "Retail grocery purchase pattern"
    if any(t in joined for t in ("notion", "openai", "github", "atlassian", "adobe", "software", "saas")):
        return "software", "SaaS or software expense pattern"
    if any(t in joined for t in ("uber", "taxi", "bahn", "train", "flight", "airline")):
        return "travel", "Transport or travel-related expense pattern"
    return "misc_review", "Unable to classify confidently with deterministic phase-1 rules"


def _decode_file_bytes(content_base64: str) -> bytes:
    try:
        return base64.b64decode(content_base64, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail={"error_code": "invalid_base64", "message": str(exc)}) from exc


def _text_from_pdf(raw: bytes) -> str:
    reader = PdfReader(BytesIO(raw))
    if reader.is_encrypted:
        reader.decrypt("")
    chunks = []
    for page in reader.pages:
        content = (page.extract_text() or "").strip()
        if content:
            chunks.append(content)
    return "\n\n".join(chunks).strip()


def _text_from_image(raw: bytes, languages: str) -> str:
    try:
        with Image.open(BytesIO(raw)) as raw_image:
            image = ImageOps.exif_transpose(raw_image)
            prepared = ImageOps.autocontrast(ImageOps.grayscale(image))
            return pytesseract.image_to_string(prepared, lang=languages, config="--oem 1 --psm 4").strip()
    except UnidentifiedImageError as exc:
        raise HTTPException(status_code=422, detail={"error_code": "unsupported_image", "message": str(exc)}) from exc


def _extract_raw_text(payload: ReceiptExtractRequest) -> str:
    raw = _decode_file_bytes(payload.file.content_base64)
    mime_type = (payload.file.mime_type or "").lower()
    languages = DEFAULT_LANGUAGES

    if mime_type == "application/pdf" or payload.file.filename.lower().endswith(".pdf") or raw.startswith(b"%PDF"):
        text = _text_from_pdf(raw)
        if len(text) >= PDF_DIRECT_TEXT_MIN_CHARS:
            return text
        # Phase 1 fallback: direct PDF text is enough for digital receipts; scanned PDFs can still be reviewed.
        return text or raw.decode("utf-8", errors="ignore").strip()

    if mime_type.startswith("image/"):
        return _text_from_image(raw, languages)

    text = raw.decode("utf-8", errors="ignore").strip()
    if text:
        return text
    return raw.decode("latin-1", errors="ignore").strip()


def _extract_structured(payload: ReceiptExtractRequest) -> dict[str, Any]:
    raw_text = _extract_raw_text(payload)
    merchant = _extract_merchant(raw_text)
    receipt_date = _find_date(raw_text)

    candidates = _money_candidates(raw_text)
    currency_from_hint = payload.hints.currency_hint.upper() if payload.hints and payload.hints.currency_hint else None
    currency = currency_from_hint
    amount = None
    if candidates:
        amount_currency, amount_value = max(candidates, key=lambda item: item[1])
        currency = currency or amount_currency
        amount = amount_value

    tax_amount = _extract_tax(raw_text)
    payment_method = _extract_payment_method(raw_text)
    category_suggestion, category_reason = _classify(raw_text, merchant)
    currency = currency or DEFAULT_CURRENCY

    field_scores = {
        "merchant": 0.95 if merchant else 0.2,
        "receipt_date": 0.95 if receipt_date else 0.2,
        "amount": 0.98 if amount is not None else 0.2,
        "tax_amount": 0.8 if tax_amount is not None else 0.55,
    }
    overall = round(sum(field_scores.values()) / len(field_scores), 2)

    reason_codes: list[str] = []
    if not merchant:
        reason_codes.append("merchant_missing")
    if not receipt_date:
        reason_codes.append("receipt_date_missing")
    if amount is None:
        reason_codes.append("amount_missing")
    if overall < CONFIDENCE_REVIEW_THRESHOLD:
        reason_codes.append("low_confidence")
    if category_suggestion == "misc_review":
        reason_codes.append("misc_review_category")
    if len(raw_text) < 20:
        reason_codes.append("ocr_quality_poor")

    signature_basis = (
        f"{merchant or ''}|{receipt_date or ''}|{amount if amount is not None else ''}|{currency}|"
        f"{hashlib.sha256(raw_text.lower().encode('utf-8')).hexdigest()}"
    )
    signature = hashlib.sha256(signature_basis.encode("utf-8")).hexdigest()
    record_id = f"rec_{uuid.uuid4().hex[:12]}"

    with _DEDUPE_LOCK:
        matched_record_id = _SIGNATURE_TO_RECORD.get(signature)
        if matched_record_id is None:
            _SIGNATURE_TO_RECORD[signature] = record_id

    is_duplicate = matched_record_id is not None
    if is_duplicate:
        reason_codes.append("possible_duplicate")

    reason_codes = sorted(set(reason_codes))
    review_required = bool(reason_codes)

    return {
        "status": "ok",
        "document_type": "receipt",
        "document_id": payload.document_id,
        "stored_record_id": record_id,
        "extracted": {
            "merchant": merchant,
            "receipt_date": receipt_date,
            "amount": amount,
            "currency": currency,
            "tax_amount": tax_amount,
            "payment_method": payment_method,
        },
        "classification": {
            "category_suggestion": category_suggestion,
            "business_relevance_note": category_reason,
        },
        "confidence": {
            "overall": overall,
            "field_scores": field_scores,
        },
        "review": {
            "required": review_required,
            "reason_codes": reason_codes,
        },
        "dedupe": {
            "suspected_duplicate": is_duplicate,
            "matched_record_id": matched_record_id,
        },
        "raw_text": raw_text,
        "schema_version": SCHEMA_VERSION,
        "processed_at": datetime.utcnow().isoformat() + "Z",
    }


def _verify_shared_token(x_processor_token: str | None) -> None:
    expected = os.getenv("PROCESSOR_SHARED_TOKEN", "").strip()
    if not expected:
        return
    if not x_processor_token or x_processor_token != expected:
        raise HTTPException(
            status_code=401,
            detail={"error_code": "internal_auth_failed", "message": "missing or invalid X-Processor-Token"},
        )


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "automation-processor"}


@app.post("/v1/receipts/extract")
def extract_receipt(payload: ReceiptExtractRequest, x_processor_token: str | None = Header(default=None)) -> dict[str, Any]:
    _verify_shared_token(x_processor_token)
    return _extract_structured(payload)
