import mimetypes
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field
from pypdf import PdfReader
from pytesseract import Output
import pytesseract

APP_VERSION = "0.1.0"
DEFAULT_LANGUAGES = os.getenv("OCR_LANGUAGES", "eng")
DIRECT_TEXT_MIN_CHARS = int(os.getenv("OCR_PDF_DIRECT_TEXT_MIN_CHARS", "80"))
MAX_UPLOAD_MB = int(os.getenv("OCR_MAX_UPLOAD_MB", "20"))
OCR_TIMEOUT_SECONDS = int(os.getenv("OCR_TIMEOUT_SECONDS", "600"))
OCR_TESSERACT_PSM = os.getenv("OCR_TESSERACT_PSM", "4")
OCR_JOBS = os.getenv("OCR_JOBS", "2")
ALLOWED_PATHS = [
    Path(raw_path.strip()).resolve()
    for raw_path in os.getenv("OCR_ALLOWED_PATHS", "/data/shared").split(",")
    if raw_path.strip()
]
SUPPORTED_IMAGE_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/tiff",
}


class OCRResponse(BaseModel):
    success: bool
    text: str = ""
    source_type: Literal["pdf", "image"] | None = None
    extraction_mode: Literal["direct_text", "ocr"] | None = None
    mime_type: str | None = None
    character_count: int = 0
    confidence: float | None = None
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    processing_ms: int = 0


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: Literal["ocr-api"]
    version: str


app = FastAPI(title="OCR API", version=APP_VERSION)


def error_response(message: str, status_code: int = 400, warnings: list[str] | None = None) -> JSONResponse:
    payload = OCRResponse(
        success=False,
        warnings=warnings or [],
        error=message,
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump())


def _is_within_allowed_paths(path: Path) -> bool:
    for allowed_path in ALLOWED_PATHS:
        if path == allowed_path or allowed_path in path.parents:
            return True
    return False


def _resolve_input_path(file_path: str) -> Path:
    path = Path(file_path).expanduser()
    if not path.is_absolute():
        raise ValueError("file_path must be absolute.")

    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("file_path must point to a file.")
    if not _is_within_allowed_paths(resolved):
        raise ValueError(
            "file_path is outside OCR_ALLOWED_PATHS. "
            f"Allowed roots: {', '.join(str(path) for path in ALLOWED_PATHS)}"
        )
    return resolved


def _save_upload(upload: UploadFile, destination: Path) -> int:
    total_bytes = 0
    limit_bytes = MAX_UPLOAD_MB * 1024 * 1024

    with destination.open("wb") as output_file:
        while True:
            chunk = upload.file.read(1024 * 1024)
            if not chunk:
                break
            total_bytes += len(chunk)
            if total_bytes > limit_bytes:
                raise ValueError(f"File is larger than OCR_MAX_UPLOAD_MB={MAX_UPLOAD_MB}.")
            output_file.write(chunk)

    return total_bytes


def _is_pdf(path: Path) -> bool:
    try:
        with path.open("rb") as file_handle:
            return file_handle.read(4) == b"%PDF"
    except OSError:
        return False


def _detect_mime(path: Path, suggested_mime: str | None = None) -> str:
    if _is_pdf(path):
        return "application/pdf"

    if suggested_mime and suggested_mime not in {"", "application/octet-stream"}:
        return suggested_mime

    guessed, _ = mimetypes.guess_type(path.name)
    if guessed:
        return guessed

    try:
        with Image.open(path) as image:
            detected = Image.MIME.get(image.format)
            if detected:
                return detected.lower()
    except (UnidentifiedImageError, OSError):
        pass

    return "application/octet-stream"


def _extract_direct_pdf_text(pdf_path: Path) -> str:
    reader = PdfReader(str(pdf_path))
    if reader.is_encrypted:
        reader.decrypt("")

    chunks: list[str] = []
    for page in reader.pages:
        content = (page.extract_text() or "").strip()
        if content:
            chunks.append(content)
    return "\n\n".join(chunks).strip()


def _run_ocrmypdf(input_path: Path, working_dir: Path, languages: str) -> str:
    ocr_output_pdf = working_dir / "ocr-output.pdf"
    sidecar_path = working_dir / "ocr-output.txt"

    command = [
        "ocrmypdf",
        "--skip-text",
        "--rotate-pages",
        "--deskew",
        "--clean",
        "--tesseract-pagesegmode",
        OCR_TESSERACT_PSM,
        "--optimize",
        "0",
        "--output-type",
        "pdf",
        "--jobs",
        OCR_JOBS,
        "--language",
        languages,
        "--sidecar",
        str(sidecar_path),
        str(input_path),
        str(ocr_output_pdf),
    ]

    process = subprocess.run(
        command,
        text=True,
        capture_output=True,
        timeout=OCR_TIMEOUT_SECONDS,
        check=False,
    )

    if process.returncode != 0:
        stderr = process.stderr.strip() or process.stdout.strip()
        raise RuntimeError(f"OCR failed with exit code {process.returncode}: {stderr[-500:]}")

    if not sidecar_path.exists():
        return ""

    return sidecar_path.read_text(encoding="utf-8", errors="ignore").strip()


def _ocr_image(image_path: Path, languages: str) -> tuple[str, float | None]:
    tesseract_config = f"--oem 1 --psm {OCR_TESSERACT_PSM}"
    with Image.open(image_path) as raw_image:
        image = ImageOps.exif_transpose(raw_image)
        grayscale = ImageOps.grayscale(image)
        prepared = ImageOps.autocontrast(grayscale)

        text = pytesseract.image_to_string(prepared, lang=languages, config=tesseract_config).strip()
        data = pytesseract.image_to_data(
            prepared,
            lang=languages,
            config=tesseract_config,
            output_type=Output.DICT,
        )

    confidences = []
    for value in data.get("conf", []):
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if parsed >= 0:
            confidences.append(parsed)

    if not confidences:
        return text, None

    average_confidence = round(sum(confidences) / len(confidences) / 100, 3)
    return text, average_confidence


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="ocr-api", version=APP_VERSION)


@app.post("/v1/extract", response_model=OCRResponse)
def extract_text(
    file: UploadFile | None = File(default=None),
    file_path: str | None = Form(default=None),
    languages: str = Form(default=DEFAULT_LANGUAGES),
) -> OCRResponse | JSONResponse:
    started_at = time.time()
    warnings: list[str] = []

    if (file is None and not file_path) or (file is not None and file_path):
        return error_response("Provide either multipart file or file_path (exactly one).")

    with tempfile.TemporaryDirectory(prefix="ocr-api-") as temp_dir:
        working_dir = Path(temp_dir)

        try:
            if file is not None:
                filename = file.filename or "upload.bin"
                input_path = working_dir / filename
                _save_upload(file, input_path)
                source_hint_mime = file.content_type
            else:
                resolved_input = _resolve_input_path(file_path or "")
                input_path = working_dir / resolved_input.name
                shutil.copy2(resolved_input, input_path)
                source_hint_mime = None
        except FileNotFoundError:
            return error_response("file_path does not exist.")
        except ValueError as error:
            return error_response(str(error))
        except OSError as error:
            return error_response(f"Could not read input file: {error}", status_code=500)

        mime_type = _detect_mime(input_path, suggested_mime=source_hint_mime)
        source_type: Literal["pdf", "image"] | None
        extraction_mode: Literal["direct_text", "ocr"] | None
        text = ""
        confidence: float | None = None

        try:
            if mime_type == "application/pdf" or input_path.suffix.lower() == ".pdf":
                source_type = "pdf"
                direct_text = _extract_direct_pdf_text(input_path)
                if len(direct_text) >= DIRECT_TEXT_MIN_CHARS:
                    extraction_mode = "direct_text"
                    text = direct_text
                else:
                    extraction_mode = "ocr"
                    if direct_text:
                        warnings.append(
                            "Embedded PDF text was below OCR_PDF_DIRECT_TEXT_MIN_CHARS; OCR fallback used."
                        )
                    else:
                        warnings.append("No embedded PDF text found; OCR fallback used.")
                    text = _run_ocrmypdf(input_path, working_dir, languages)
                    if not text:
                        warnings.append("OCR produced empty text.")
            else:
                source_type = "image"
                if mime_type not in SUPPORTED_IMAGE_MIME_TYPES:
                    warnings.append(f"Mime type {mime_type} is not in the preferred image list, trying OCR anyway.")
                extraction_mode = "ocr"
                text, confidence = _ocr_image(input_path, languages)
                if not text:
                    warnings.append("OCR produced empty text.")
        except UnidentifiedImageError:
            return error_response("Unsupported file type. Use PDF, JPEG, PNG, WebP, or TIFF.")
        except RuntimeError as error:
            return error_response(str(error), status_code=500, warnings=warnings)
        except Exception as error:
            return error_response(f"Unexpected OCR error: {error}", status_code=500, warnings=warnings)

        payload = OCRResponse(
            success=True,
            text=text,
            source_type=source_type,
            extraction_mode=extraction_mode,
            mime_type=mime_type,
            character_count=len(text),
            confidence=confidence,
            warnings=warnings,
            processing_ms=int((time.time() - started_at) * 1000),
        )
        return payload
