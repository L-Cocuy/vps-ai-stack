import base64
import importlib.util
import os
import re
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SERVICE_MAIN = ROOT / "services" / "automation-processor" / "app" / "main.py"
COMPOSE = ROOT / "docker-compose.yml"
REQUIRED_SHEET_COLUMNS = [
    "record_id",
    "created_at",
    "source_channel",
    "source_message_id",
    "source_sender",
    "source_filename",
    "drive_file_id",
    "drive_web_url",
    "merchant",
    "receipt_date",
    "amount",
    "currency",
    "tax_amount",
    "payment_method",
    "category_suggestion",
    "category_reason",
    "confidence_score",
    "review_required",
    "review_reasons",
    "duplicate_flag",
    "status",
    "raw_text_excerpt",
]


def _load_module(module_name="automation_processor_main"):
    assert SERVICE_MAIN.exists(), "UC01 requires services/automation-processor/app/main.py"
    sys.path.insert(0, str(SERVICE_MAIN.parent.parent))
    spec = importlib.util.spec_from_file_location(module_name, SERVICE_MAIN)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _load_app():
    return _load_module().app


def _client():
    os.environ["PROCESSOR_SHARED_TOKEN"] = "test-token"
    return TestClient(_load_app())


def _receipt_payload(text: str, *, document_id="doc_test", source_channel="gmail"):
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return {
        "document_id": document_id,
        "file": {
            "filename": "receipt.txt",
            "mime_type": "text/plain",
            "content_base64": encoded,
        },
        "source": {
            "channel": source_channel,
            "source_message_id": "msg-123",
            "sender": "juan@example.com",
        },
        "storage": {
            "drive_file_id": "drive-file-123",
            "drive_web_url": "https://drive.google.com/file/d/drive-file-123/view",
        },
        "hints": {
            "language_hint": ["de", "en"],
            "currency_hint": "EUR",
            "timezone": "Europe/Vienna",
        },
    }


def test_compose_exposes_automation_processor_only_internally():
    compose = COMPOSE.read_text(encoding="utf-8")
    assert "automation-processor:" in compose
    match = re.search(r"(?ms)^  automation-processor:\n(?P<block>.*?)(?=^  [a-zA-Z0-9_-]+:|\Z)", compose)
    assert match, "compose must define an automation-processor service"
    block = match.group("block")
    assert "expose:" in block and '"8081"' in block
    assert "ports:" not in block, "processor must not publish a public port"
    assert "traefik.enable=true" not in block, "processor must not be routed publicly via Traefik"
    assert "PROCESSOR_SHARED_TOKEN" in block
    assert "OLLAMA_BASE_URL" in block
    assert "OCR_LANGUAGES" in block and "eng+deu" in block
    assert "PROCESSOR_BASE_URL: http://automation-processor:8081" in compose
    assert "N8N_OCR_BASE_URL" not in compose, "n8n should call the UC01 processor contract, not the old OCR-only API"


def test_healthz_contract():
    response = _client().get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["service"] == "automation-processor"


def test_clean_receipt_extracts_finance_fields_and_needs_no_review():
    text = """
    BILLA Wien Mitte
    Datum: 12.04.2026
    Summe EUR 18,90
    MwSt 10% EUR 1,72
    Zahlung: Karte
    """
    response = _client().post(
        "/v1/receipts/extract",
        json=_receipt_payload(text, document_id="doc_clean", source_channel="telegram"),
        headers={"X-Processor-Token": "test-token"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["stored_record_id"].startswith("rec_")
    assert body["extracted"]["merchant"] == "BILLA Wien Mitte"
    assert body["extracted"]["receipt_date"] == "2026-04-12"
    assert body["extracted"]["amount"] == 18.90
    assert body["extracted"]["currency"] == "EUR"
    assert body["extracted"]["tax_amount"] == 1.72
    assert body["classification"]["category_suggestion"]
    assert body["confidence"]["overall"] >= 0.75
    assert body["review"]["required"] is False
    assert body["dedupe"]["suspected_duplicate"] is False
    assert "BILLA" in body["raw_text"]


def test_incomplete_receipt_returns_machine_readable_review_reasons():
    response = _client().post(
        "/v1/receipts/extract",
        json=_receipt_payload("blurred cropped paper slip\nEUR", document_id="doc_messy"),
        headers={"X-Processor-Token": "test-token"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["review"]["required"] is True
    assert body["confidence"]["overall"] < 0.75
    assert set(body["review"]["reason_codes"]) >= {"merchant_missing", "receipt_date_missing", "amount_missing"}
    assert body["extracted"]["currency"] == "EUR"


def test_duplicate_hint_is_stable_for_same_receipt_signature():
    client = _client()
    payload = _receipt_payload(
        "ACME Store\n2026-04-12\nTotal EUR 42.00",
        document_id="doc_dup_1",
    )
    first = client.post("/v1/receipts/extract", json=payload, headers={"X-Processor-Token": "test-token"})
    assert first.status_code == 200, first.text

    second_payload = _receipt_payload(
        "ACME Store\n2026-04-12\nTotal EUR 42.00",
        document_id="doc_dup_2",
    )
    second = client.post("/v1/receipts/extract", json=second_payload, headers={"X-Processor-Token": "test-token"})
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["dedupe"]["suspected_duplicate"] is True
    assert body["dedupe"]["matched_record_id"] == first.json()["stored_record_id"]


def test_ollama_structuring_corrects_openai_invoice_ocr_that_heuristics_misread(monkeypatch):
    os.environ["PROCESSOR_SHARED_TOKEN"] = "test-token"
    os.environ["OLLAMA_MODEL"] = "llama3.2"
    module = _load_module("automation_processor_openai_ocr_test")
    calls = []

    def fake_ollama_extract(raw_text, hints=None):
        calls.append(raw_text)
        return {
            "merchant": "OpenAI OpCo, LLC",
            "receipt_date": "2026-01-01",
            "amount": 20.00,
            "currency": "USD",
            "tax_amount": None,
            "payment_method": "card",
            "category_suggestion": "software",
            "business_relevance_note": "OpenAI subscription/API invoice",
            "confidence": 0.93,
            "review_required": False,
            "review_reasons": [],
        }

    monkeypatch.setattr(module, "_call_ollama_extract", fake_ollama_extract)
    client = TestClient(module.app)
    response = client.post(
        "/v1/receipts/extract",
        json=_receipt_payload(
            """Page 1 of 1
Receipt
Invoice number 877B74AA-0031
Receipt number 2604-9803-6736
Date paid January 1, 2026
OpenAI OpCo, LLC
EU OSS VAT EU372041333
Bill to Juan Pablo Mejia Nino
$20.00 paid on January 1, 2026
Description Qty Unit price Tax Amount""",
            document_id="doc_openai_real_ocr",
            source_channel="telegram",
        ),
        headers={"X-Processor-Token": "test-token"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert calls, "processor must route OCR text through Ollama before filling the finance fields"
    assert body["extracted"] == {
        "merchant": "OpenAI OpCo, LLC",
        "receipt_date": "2026-01-01",
        "amount": 20.0,
        "currency": "USD",
        "tax_amount": None,
        "payment_method": "card",
    }
    assert body["classification"]["category_suggestion"] == "software"
    assert body["classification"]["category_reason"] == "OpenAI subscription/API invoice"
    assert body["classification"]["business_relevance_note"] == "OpenAI subscription/API invoice"
    assert body["confidence"]["overall"] >= 0.9
    assert body["review"]["required"] is False
    assert body["extraction"]["engine"] == "ollama"
    assert body["extraction"]["model"] == "llama3.2"


def test_ollama_structuring_extracts_spusu_total_from_real_ocr(monkeypatch):
    os.environ["PROCESSOR_SHARED_TOKEN"] = "test-token"
    module = _load_module("automation_processor_spusu_ocr_test")
    calls = []

    def fake_ollama_extract(raw_text, hints=None):
        calls.append(raw_text)
        return {
            "merchant": "spusu",
            "receipt_date": "2026-04-01",
            "amount": 9.90,
            "currency": "EUR",
            "tax_amount": 1.65,
            "payment_method": None,
            "category_suggestion": "telecom",
            "business_relevance_note": "Mobile phone service invoice",
            "confidence": 0.91,
            "review_required": False,
            "review_reasons": [],
        }

    monkeypatch.setattr(module, "_call_ollama_extract", fake_ollama_extract)
    client = TestClient(module.app)
    response = client.post(
        "/v1/receipts/extract",
        json=_receipt_payload(
            """spusu
DC Tower 1, 45. Stock
Donau-City-Straße 7
1220 Wien
Rechnung Wien, 01.04.2026
Kundennummer: 4360507
Belegnummer: SR-2140225/2026
1 spusu 5G 12.000 0676 347 9670 Juan Mejia € 9,90
Gesamtbetrag inkl. USt
davon 20,00% USt € 1,65 - Nettobetrag € 8,25
€ 9,90""",
            document_id="doc_spusu_real_ocr",
            source_channel="gmail",
        ),
        headers={"X-Processor-Token": "test-token"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert calls, "processor must route OCR text through Ollama before filling the finance fields"
    assert body["extracted"]["merchant"] == "spusu"
    assert body["extracted"]["receipt_date"] == "2026-04-01"
    assert body["extracted"]["amount"] == 9.90
    assert body["extracted"]["tax_amount"] == 1.65
    assert body["classification"]["category_suggestion"] == "telecom"
    assert body["classification"]["category_reason"] == "Mobile phone service invoice"
    assert body["classification"]["business_relevance_note"] == "Mobile phone service invoice"
    assert body["review"]["required"] is False
    assert body["extraction"]["engine"] == "ollama"


def test_heuristic_fallback_handles_real_openai_invoice_when_ollama_is_unavailable(monkeypatch):
    os.environ["PROCESSOR_SHARED_TOKEN"] = "test-token"
    module = _load_module("automation_processor_openai_fallback_test")

    def unavailable(raw_text, hints=None):
        raise RuntimeError("ollama unavailable")

    monkeypatch.setattr(module, "_call_ollama_extract", unavailable)
    client = TestClient(module.app)
    response = client.post(
        "/v1/receipts/extract",
        json=_receipt_payload(
            """Page 1 of 1
Receipt
Invoice number 877B74AA-0031
Receipt number 2604-9803-6736
Date paid January 1, 2026
OpenAI OpCo, LLC
EU OSS VAT EU372041333
Bill to Juan Pablo Mejia Nino
$20.00 paid on January 1, 2026
Description Qty Unit price Tax Amount""",
            document_id="doc_openai_fallback",
        ),
        headers={"X-Processor-Token": "test-token"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["extracted"]["merchant"] == "OpenAI OpCo, LLC"
    assert body["extracted"]["receipt_date"] == "2026-01-01"
    assert body["extracted"]["amount"] == 20.0
    assert body["extracted"]["currency"] == "EUR"  # hint wins in fallback mode
    assert body["extracted"]["tax_amount"] is None
    assert body["extraction"]["engine"] == "heuristic"
    assert body["extraction"]["fallback_used"] is True


def test_heuristic_fallback_handles_real_spusu_invoice_when_ollama_is_unavailable(monkeypatch):
    os.environ["PROCESSOR_SHARED_TOKEN"] = "test-token"
    module = _load_module("automation_processor_spusu_fallback_test")

    def unavailable(raw_text, hints=None):
        raise RuntimeError("ollama unavailable")

    monkeypatch.setattr(module, "_call_ollama_extract", unavailable)
    client = TestClient(module.app)
    response = client.post(
        "/v1/receipts/extract",
        json=_receipt_payload(
            """spusu
DC Tower 1, 45. Stock
Donau-City-Straße 7
1220 Wien
Rechnung Wien, 01.04.2026
Kundennummer: 4360507
Belegnummer: SR-2140225/2026
1 spusu 5G 12.000 0676 347 9670 Juan Mejia € 9,90
Gesamtbetrag inkl. USt
davon 20,00% USt € 1,65 - Nettobetrag € 8,25
€ 9,90""",
            document_id="doc_spusu_fallback",
        ),
        headers={"X-Processor-Token": "test-token"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["extracted"]["merchant"] == "spusu"
    assert body["extracted"]["receipt_date"] == "2026-04-01"
    assert body["extracted"]["amount"] == 9.90
    assert body["extracted"]["tax_amount"] == 1.65
    assert body["extraction"]["engine"] == "heuristic"
    assert body["extraction"]["fallback_used"] is True


def test_processor_rejects_missing_or_wrong_shared_token():
    client = _client()
    payload = _receipt_payload("ACME\nTotal EUR 10.00")
    assert client.post("/v1/receipts/extract", json=payload).status_code == 401
    assert client.post(
        "/v1/receipts/extract",
        json=payload,
        headers={"X-Processor-Token": "wrong"},
    ).status_code == 401


def test_sheet_columns_reference_is_present_for_n8n_mapping():
    schema = ROOT / "docs" / "uc01-receipts-sheet-columns.md"
    assert schema.exists(), "Document the phase-1 Google Sheets receipts columns in docs/"
    content = schema.read_text(encoding="utf-8")
    for column in REQUIRED_SHEET_COLUMNS:
        assert f"`{column}`" in content
