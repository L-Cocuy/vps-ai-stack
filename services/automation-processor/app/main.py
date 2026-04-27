import base64
import hashlib
import json
import logging
import os
import re
import threading
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, field_validator
from pypdf import PdfReader
import pytesseract

SCHEMA_VERSION = "2026-04-12.receipt.v1"
CONFIDENCE_REVIEW_THRESHOLD = 0.75
DEFAULT_CURRENCY = os.getenv("DEFAULT_CURRENCY", "EUR")
DEFAULT_LANGUAGES = os.getenv("OCR_LANGUAGES", "eng+deu")
PDF_DIRECT_TEXT_MIN_CHARS = int(os.getenv("OCR_PDF_DIRECT_TEXT_MIN_CHARS", "80"))
OLLAMA_PROMPT_VERSION = "uc01-receipt-json-v1"
MAX_UPLOAD_BYTES = int(os.getenv("OCR_MAX_UPLOAD_MB", "20")) * 1024 * 1024

MONTHS = {
    "january": "01",
    "february": "02",
    "march": "03",
    "april": "04",
    "may": "05",
    "june": "06",
    "july": "07",
    "august": "08",
    "september": "09",
    "october": "10",
    "november": "11",
    "december": "12",
}

CURRENCY_SYMBOLS = {"€": "EUR", "$": "USD", "£": "GBP"}

_SIGNATURE_TO_RECORD: dict[str, str] = {}
_DEDUPE_LOCK = threading.Lock()

_logger = logging.getLogger("automation-processor")

if not os.getenv("PROCESSOR_SHARED_TOKEN", "").strip():
    _logger.warning(
        "PROCESSOR_SHARED_TOKEN is not set — /v1/receipts/extract is unauthenticated. "
        "Set this variable before deploying."
    )

app = FastAPI(title="automation-processor", version="0.1.0")


class FilePayload(BaseModel):
    filename: str
    mime_type: str | None = None
    content_base64: str


class SourcePayload(BaseModel):
    channel: str
    source_message_id: str | int | None = None
    sender: str | dict | list | None = None
    source_user: str | dict | list | None = None
    subject: str | dict | list | None = None

    @staticmethod
    def _stringify_source_value(value):
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float, bool)):
            return str(value)
        if isinstance(value, dict):
            for key in ("text", "email", "address", "name", "html", "value"):
                if key in value and value[key] not in (None, ""):
                    nested = SourcePayload._stringify_source_value(value[key])
                    if nested:
                        return nested
            return str(value)
        if isinstance(value, list):
            parts = [SourcePayload._stringify_source_value(item) for item in value]
            return ", ".join(part for part in parts if part)
        return str(value)

    @field_validator("source_message_id", "sender", "source_user", "subject", mode="before")
    @classmethod
    def normalize_source_strings(cls, value):
        return cls._stringify_source_value(value)


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

    english = re.search(
        r"\b(" + "|".join(MONTHS) + r")\s+(\d{1,2}),\s*(20\d{2})\b",
        raw_text,
        re.IGNORECASE,
    )
    if english:
        month_name, day, year = english.groups()
        return f"{year}-{MONTHS[month_name.lower()]}-{int(day):02d}"
    return None


def _money_candidates(raw_text: str) -> list[tuple[str, float]]:
    candidates: list[tuple[str, float]] = []
    patterns = [
        re.compile(r"\b(EUR|USD|GBP|CHF)\b[^\d]{0,8}([0-9][0-9.,]*)", re.IGNORECASE),
        re.compile(r"([0-9][0-9.,]*)[^\w]{0,3}\b(EUR|USD|GBP|CHF)\b", re.IGNORECASE),
        re.compile(r"([€$£])\s*([0-9][0-9.,]*)"),
        re.compile(r"([0-9][0-9.,]*)\s*([€$£])"),
    ]
    for pattern in patterns:
        for match in pattern.finditer(raw_text):
            left, right = match.group(1), match.group(2)
            if left in CURRENCY_SYMBOLS:
                currency = CURRENCY_SYMBOLS[left]
                number = right
            elif right in CURRENCY_SYMBOLS:
                currency = CURRENCY_SYMBOLS[right]
                number = left
            elif re.match(r"^[A-Za-z]{3}$", left):
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
        "page ",
        "invoice number",
        "receipt number",
        "bill to",
        "description",
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
        if any(token in lowered for token in ("mwst", "ust", "tax")) or ("vat" in lowered and any(symbol in line for symbol in ("€", "$", "£"))):
            monetary = _money_candidates(line)
            if monetary:
                parsed = monetary[0][1]
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
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail={"error_code": "file_too_large", "message": f"File exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit"},
        )
    mime_type = (payload.file.mime_type or "").lower()
    languages = DEFAULT_LANGUAGES

    if mime_type == "application/pdf" or payload.file.filename.lower().endswith(".pdf") or raw.startswith(b"%PDF"):
        text = _text_from_pdf(raw)
        if len(text) >= PDF_DIRECT_TEXT_MIN_CHARS:
            return text
        # Scanned PDFs with insufficient extractable text: return what we have (may be empty).
        # The review flow catches this via ocr_quality_poor. Full scanned-PDF rendering is a phase-2 gap.
        return text

    if mime_type.startswith("image/"):
        return _text_from_image(raw, languages)

    text = raw.decode("utf-8", errors="ignore").strip()
    if text:
        return text
    return raw.decode("latin-1", errors="ignore").strip()


def _json_type_name(value: Any) -> str:
    return type(value).__name__


def _normalize_ollama_extract(candidate: Any) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        raise ValueError(f"Ollama extraction must be a JSON object, got {_json_type_name(candidate)}")

    allowed_payment_methods = {"card", "cash", "bank_transfer", "other"}
    normalized: dict[str, Any] = {}

    for key in ("merchant", "receipt_date", "currency", "payment_method", "category_suggestion", "business_relevance_note"):
        value = candidate.get(key)
        normalized[key] = value.strip() if isinstance(value, str) and value.strip() else None

    if normalized["receipt_date"] and not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", normalized["receipt_date"]):
        normalized["receipt_date"] = None

    if normalized["currency"]:
        normalized["currency"] = normalized["currency"].upper()
        if not re.fullmatch(r"[A-Z]{3}", normalized["currency"]):
            normalized["currency"] = None

    if normalized["payment_method"] and normalized["payment_method"] not in allowed_payment_methods:
        normalized["payment_method"] = "other"

    for key in ("amount", "tax_amount", "confidence"):
        value = candidate.get(key)
        if value is None or value == "":
            normalized[key] = None
            continue
        if isinstance(value, str):
            value = _parse_decimal(value)
        try:
            normalized[key] = round(float(value), 2)
        except (TypeError, ValueError):
            normalized[key] = None

    if normalized["confidence"] is not None:
        normalized["confidence"] = max(0.0, min(1.0, float(normalized["confidence"])))

    normalized["review_required"] = bool(candidate.get("review_required", False))
    review_reasons = candidate.get("review_reasons") or []
    if isinstance(review_reasons, str):
        review_reasons = [review_reasons]
    if not isinstance(review_reasons, list):
        review_reasons = []
    normalized["review_reasons"] = sorted({str(reason).strip() for reason in review_reasons if str(reason).strip()})
    return normalized


def _call_ollama_extract(raw_text: str, hints: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Ask local Ollama to structure receipt OCR text as strict JSON.

    The processor remains credential-free and internal-only; this calls the local Ollama API on the Docker network.
    """
    base_url = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434").rstrip("/")
    model = os.getenv("OLLAMA_MODEL", "llama3.2")
    timeout = float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "45"))
    hints = hints or {}
    prompt = f"""You extract accounting fields from receipt OCR text.
Return ONLY one valid JSON object. Do not include markdown or commentary.
Use null when unsure. Do not invent values.
Normalize receipt_date as ISO YYYY-MM-DD. Normalize amount/tax_amount as numbers. Normalize currency as ISO 4217.
Allowed payment_method values: card, cash, bank_transfer, other, null.
Required JSON keys: merchant, receipt_date, amount, currency, tax_amount, payment_method, category_suggestion, business_relevance_note, confidence, review_required, review_reasons.
confidence must be a number from 0 to 1. review_reasons must be an array of machine-readable strings.
Hints JSON: {json.dumps(hints, ensure_ascii=False)}
OCR text:
{raw_text[:12000]}
"""
    request_body = json.dumps({"model": model, "prompt": prompt, "stream": False, "format": "json"}).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/api/generate",
        data=request_body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"ollama_request_failed: {exc}") from exc

    content = payload.get("response", payload)
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"ollama_invalid_json: {exc}") from exc
    return _normalize_ollama_extract(content)


def _heuristic_extract(raw_text: str, payload: ReceiptExtractRequest) -> dict[str, Any]:
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
            "category_reason": category_reason,
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
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }


def _hints_dict(hints: HintsPayload | None) -> dict[str, Any]:
    if hints is None:
        return {}
    return {key: value for key, value in hints.model_dump().items() if value is not None}


def _apply_ollama_values(response: dict[str, Any], ollama_values: dict[str, Any]) -> dict[str, Any]:
    extracted = response["extracted"]
    heuristic_amount = extracted.get("amount")
    heuristic_tax_amount = extracted.get("tax_amount")

    if (
        ollama_values.get("amount") is not None
        and heuristic_amount is not None
        and ollama_values["amount"] > heuristic_amount * 10
    ):
        ollama_values["amount"] = heuristic_amount

    if (
        ollama_values.get("tax_amount") is not None
        and heuristic_tax_amount is not None
        and ollama_values["tax_amount"] > max(heuristic_tax_amount * 10, (ollama_values.get("amount") or heuristic_amount or 0))
    ):
        ollama_values["tax_amount"] = heuristic_tax_amount

    for key in ("merchant", "receipt_date", "amount", "currency"):
        if key in ollama_values and ollama_values[key] is not None:
            extracted[key] = ollama_values[key]
    for key in ("tax_amount", "payment_method"):
        if key in ollama_values:
            extracted[key] = ollama_values[key]

    if ollama_values.get("category_suggestion"):
        response["classification"]["category_suggestion"] = ollama_values["category_suggestion"]
    if ollama_values.get("business_relevance_note"):
        response["classification"]["business_relevance_note"] = ollama_values["business_relevance_note"]
        response["classification"]["category_reason"] = ollama_values["business_relevance_note"]

    confidence = ollama_values.get("confidence")
    if confidence is not None:
        response["confidence"]["overall"] = round(float(confidence), 2)
        response["confidence"]["field_scores"] = {
            "merchant": 0.95 if extracted.get("merchant") else 0.2,
            "receipt_date": 0.95 if extracted.get("receipt_date") else 0.2,
            "amount": 0.98 if extracted.get("amount") is not None else 0.2,
            "tax_amount": 0.8 if extracted.get("tax_amount") is not None else 0.55,
        }

    review_reasons = list(ollama_values.get("review_reasons") or [])
    if response["confidence"]["overall"] < CONFIDENCE_REVIEW_THRESHOLD and "low_confidence" not in review_reasons:
        review_reasons.append("low_confidence")
    response["review"] = {
        "required": bool(ollama_values.get("review_required", False) or review_reasons),
        "reason_codes": sorted(set(review_reasons)),
    }
    return response


def _extract_structured(payload: ReceiptExtractRequest) -> dict[str, Any]:
    raw_text = _extract_raw_text(payload)
    response = _heuristic_extract(raw_text, payload)
    model = os.getenv("OLLAMA_MODEL", "llama3.2")

    try:
        ollama_values = _call_ollama_extract(raw_text, _hints_dict(payload.hints))
        if not ollama_values:
            raise ValueError("ollama_empty_response")
        response = _apply_ollama_values(response, ollama_values)
        response["extraction"] = {
            "engine": "ollama",
            "model": model,
            "prompt_version": OLLAMA_PROMPT_VERSION,
            "fallback_used": False,
        }
    except Exception as exc:  # noqa: BLE001 - fallback must be resilient for phase-1 automation
        response["extraction"] = {
            "engine": "heuristic",
            "model": model,
            "prompt_version": OLLAMA_PROMPT_VERSION,
            "fallback_used": True,
            "fallback_reason": str(exc),
        }

    return response


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
