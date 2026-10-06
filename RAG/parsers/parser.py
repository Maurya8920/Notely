import os
import json
import time
from pathlib import Path
from rich import print

os.environ["TORCHDYNAMO_DISABLE"] = "1"

_rapid_engine = None
_converter = None


def get_rapid_engine():
    """Load OCR engine once and reuse across requests."""
    global _rapid_engine
    if _rapid_engine is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
            _rapid_engine = RapidOCR()
        except Exception:
            try:
                import easyocr
                _rapid_engine = easyocr.Reader(["en"])
            except Exception as e:
                print(f"[warning] Failed to initialize OCR engine: {e}")
                return None
    return _rapid_engine


def build_converter():
    """Build Docling converter components."""
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.pipeline_options import (
        PdfPipelineOptions,
        AcceleratorOptions,
        AcceleratorDevice,
        TableFormerMode,
        RapidOcrOptions,
    )
    from docling.datamodel.base_models import InputFormat

    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = True
    pipeline_options.ocr_options = RapidOcrOptions(force_full_page_ocr=False)
    pipeline_options.do_table_structure = True
    pipeline_options.table_structure_options.mode = TableFormerMode.FAST
    pipeline_options.generate_picture_images = True
    pipeline_options.images_scale = 2.0
    pipeline_options.accelerator_options = AcceleratorOptions(
        num_threads=min(2, os.cpu_count() or 1),
        device=AcceleratorDevice.CPU,
    )

    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
        }
    )


def get_converter():
    """Reuse a single DocumentConverter singleton across requests."""
    global _converter
    if _converter is None:
        _converter = build_converter()
    return _converter


def ocr_image(pil_image) -> str:
    """Runs OCR on a single PIL image, returns extracted text."""
    import numpy as np
    engine = get_rapid_engine()
    if engine is None:
        return ""

    img_array = np.array(pil_image)
    if hasattr(engine, "__call__"):
        try:
            result, _ = engine(img_array)
            if not result:
                return ""
            return " ".join([line[1] for line in result if line and len(line) > 1])
        except Exception as e:
            print(f"[OCR error]: {e}")
    if hasattr(engine, "readtext"):
        try:
            results = engine.readtext(img_array)
            return " ".join([r[1] for r in results if len(r) > 1])
        except Exception as e:
            print(f"[OCR error]: {e}")
    return ""


def extract_pictures(result, pdf_stem: str) -> list[dict]:
    """
    Runs OCR on each embedded picture Docling extracted.
    """
    pictures = getattr(result.document, "pictures", [])
    if not pictures:
        return []

    image_chunks = []
    for i, picture in enumerate(pictures):
        pil_image = getattr(picture.image, "pil_image", None)
        if pil_image is None:
            continue

        print(f"  OCR-ing embedded image {i}...")
        extracted_text = ocr_image(pil_image)

        prov = picture.prov[0] if picture.prov else None
        image_chunks.append(
            {
                "image_id": f"img_{i:03d}",
                "page": prov.page_no if prov else None,
                "image_width": pil_image.width,
                "image_height": pil_image.height,
                "extracted_text": extracted_text,
            }
        )
        print(f"    → {len(extracted_text)} chars extracted")

    return image_chunks


def parse_pdf_fast(file_path: str, max_pages: int | None = None) -> tuple[str, list[dict]]:
    """
    Fast extraction path for PDFs using pypdfium2:
    - Text-based pages have their text extracted in milliseconds.
    - Scanned pages (< 40 characters) are rendered to image and run through OCR.
    """
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(file_path)
    total_pages = len(pdf)
    limit = min(total_pages, max_pages) if max_pages else total_pages

    page_blocks = []
    image_chunks = []
    has_meaningful_text = False

    for page_idx in range(limit):
        page = pdf[page_idx]
        textpage = page.get_textpage()
        text = textpage.get_text_range().strip()

        # If selectable text layer exists and has substantial content (>40 chars)
        if len(text) >= 40:
            has_meaningful_text = True
            page_blocks.append(f"## Page {page_idx + 1}\n\n{text}")
        else:
            # Scanned page or low-text page: run OCR on rendered page
            try:
                pil_image = page.render(scale=2.0).to_pil()
                ocr_text = ocr_image(pil_image)
                if ocr_text.strip():
                    has_meaningful_text = True
                    page_blocks.append(f"## Page {page_idx + 1}\n\n{ocr_text.strip()}")
                    image_chunks.append({
                        "image_id": f"scan_page_{page_idx + 1:03d}",
                        "page": page_idx + 1,
                        "image_width": pil_image.width,
                        "image_height": pil_image.height,
                        "extracted_text": ocr_text.strip(),
                    })
                elif text:
                    page_blocks.append(f"## Page {page_idx + 1}\n\n{text}")
            except Exception as e:
                print(f"  [OCR page {page_idx + 1} failed]: {e}")
                if text:
                    page_blocks.append(f"## Page {page_idx + 1}\n\n{text}")

    full_text = "\n\n".join(page_blocks).strip()
    return full_text, image_chunks


def parse_document(
    file_path: str, max_pages: int | None = None
) -> tuple[str, list[dict]]:
    """
    Intelligent document parser:
    1. Text / Markdown files: direct read.
    2. Image files (.png, .jpg, etc.): OCR directly.
    3. PDFs: fast text-extraction path (pypdfium2), OCR only scanned pages.
    4. Complex/Other (.docx, .pptx) or fallback: cached Docling converter.
    """
    path = Path(file_path)
    ext = path.suffix.lower()

    # 1. Plain text / Markdown
    if ext in [".txt", ".md", ".csv", ".json", ".log"]:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            return content, []
        except Exception as e:
            print(f"[text read error]: {e}")

    # 2. Standalone image files
    if ext in [".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"]:
        from PIL import Image
        try:
            with Image.open(file_path) as pil_img:
                extracted = ocr_image(pil_img)
                image_chunks = [{
                    "image_id": "img_001",
                    "page": 1,
                    "image_width": pil_img.width,
                    "image_height": pil_img.height,
                    "extracted_text": extracted,
                }]
                return f"**Text from image ({path.name}):**\n\n{extracted}", image_chunks
        except Exception as e:
            print(f"[image OCR error]: {e}")

    # 3. PDF: use fast text-extraction path first
    if ext == ".pdf":
        try:
            print(f"[parser] Running fast PDF text extraction on {path.name}...")
            t0 = time.time()
            text, image_chunks = parse_pdf_fast(file_path, max_pages=max_pages)
            if text and len(text.strip()) > 50:
                print(f"[parser] Fast extraction completed in {time.time() - t0:.2f}s ({len(text)} chars)")
                return text, image_chunks
            print("[parser] Fast extraction yielded minimal text, falling back to Docling...")
        except Exception as e:
            print(f"[parser] Fast PDF extraction failed ({e}), falling back to Docling...")

    # 4. Fallback / Rich formats (DOCX, PPTX, complex layouts): use cached Docling
    converter = get_converter()

    if max_pages:
        result = converter.convert(file_path, max_num_pages=max_pages)
    else:
        result = converter.convert(file_path)

    markdown = result.document.export_to_markdown()

    pdf_stem = Path(file_path).stem
    print(f"\nExtracting images ({len(getattr(result.document, 'pictures', []))} found)...")
    image_chunks = extract_pictures(result, pdf_stem)

    if image_chunks:
        image_text_blocks = [
            f"**Image (page {c['page']}):**\n{c['extracted_text']}"
            for c in image_chunks
            if c["extracted_text"].strip()
        ]
        if image_text_blocks:
            markdown += "\n\n**Text extracted from images:**\n" + "\n\n".join(
                image_text_blocks
            )

    return markdown, image_chunks
