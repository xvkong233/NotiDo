"""Bounded document decoding subprocess; never executes macros or external relations."""

import json
import subprocess
import sys
import zipfile
from pathlib import Path

from PIL import Image


def read(path: Path, suffix: str, output: Path, budget):
    result = {"segments": [], "unknowns": [], "visuals": []}
    total = 0

    def segment(location, text):
        nonlocal total
        total += len(text)
        if total > budget["document_characters"]:
            result["unknowns"].append(f"TEXT_LIMIT:{location}")
        else:
            result["segments"].append({"location": location, "text": text})

    def image(path, location):
        with Image.open(path) as im:
            if im.width * im.height > budget["image_pixels"]:
                result["unknowns"].append(f"PIXEL_LIMIT:{location}")
                return
            im.verify()
        # Normalize inside the bounded worker, so rendering is charged and
        # cancellable together with decoding; the immutable original is intact.
        destination = output / f"visual-{len(result['visuals']) + 1}.png"
        with Image.open(path) as im:
            im.convert("RGB").save(destination, format="PNG")
        result["visuals"].append({"location": location, "path": str(destination)})

    if suffix == ".txt":
        data = path.read_bytes()
        try:
            segment("text", data.decode("utf-8-sig"))
        except UnicodeError:
            result["unknowns"].append("ENCODING_UNKNOWN")
    elif suffix in (".png", ".jpg", ".jpeg", ".webp"):
        image(path, "image")
    elif suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(path)
        if reader.is_encrypted:
            result["unknowns"].append("PDF_ENCRYPTED")
            return result
        if len(reader.pages) > budget["pdf_pages"]:
            result["unknowns"].append(
                f"PDF_PAGE_LIMIT:{budget['pdf_pages'] + 1}-{len(reader.pages)}"
            )
        for index, page in enumerate(reader.pages[: budget["pdf_pages"]], start=1):
            location = f"page:{index}"
            text = page.extract_text() or ""
            segment(location, text)
            # Any page with images is retained for visual reading, including tables/charts.
            if not text.strip() or list(page.images):
                destination = output / f"page-{index}"
                try:
                    subprocess.run(
                        [
                            "pdftoppm",
                            "-f",
                            str(index),
                            "-l",
                            str(index),
                            "-scale-to",
                            "1800",
                            "-singlefile",
                            "-png",
                            str(path),
                            str(destination),
                        ],
                        check=True,
                        timeout=8,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    image(destination.with_suffix(".png"), location)
                except (OSError, subprocess.SubprocessError):
                    result["unknowns"].append(f"PDF_VISUAL_UNAVAILABLE:{location}")
    elif suffix == ".docx":
        # Bound zip expansion before python-docx parses internal XML. No ZIP attachment extraction.
        with zipfile.ZipFile(path) as archive:
            info = archive.infolist()
            if sum(x.file_size for x in info) > 100 * 1024**2 or len(info) > 1000:
                result["unknowns"].append("DOCX_EXPANSION_LIMIT")
                return result
            from docx import Document
            from docx.table import Table

            document = Document(path)
            for index, block in enumerate(document.iter_inner_content(), start=1):
                text = (
                    "\n".join("\t".join(cell.text for cell in row.cells) for row in block.rows)
                    if isinstance(block, Table)
                    else block.text
                )
                segment(f"block:{index}", text)
            embedded = [x for x in info if x.filename.startswith("word/media/")]
            if len(embedded) > budget["docx_visuals"]:
                result["unknowns"].append(
                    f"DOCX_IMAGE_LIMIT:{budget['docx_visuals'] + 1}-{len(embedded)}"
                )
            for index, entry in enumerate(embedded[: budget["docx_visuals"]], start=1):
                destination = output / f"embedded-{index}{Path(entry.filename).suffix}"
                destination.write_bytes(archive.read(entry))
                try:
                    image(destination, f"embedded:{index}")
                except Exception:
                    result["unknowns"].append(f"DOCX_IMAGE_UNREADABLE:{index}")
    else:
        result["unknowns"].append("BODY_NOT_SUPPORTED_ATTACHMENT_ONLY")
    return result


if __name__ == "__main__":
    try:
        result = read(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), json.loads(sys.argv[4]))
    except Exception:
        result = {"segments": [], "unknowns": ["CORRUPT_OR_UNREADABLE"], "visuals": []}
    print(json.dumps(result, ensure_ascii=False))
