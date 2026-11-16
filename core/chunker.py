"""Lossless, bounded chunking for UTF-8 text/Markdown and Word documents."""
import os
import re

TERMINATORS = ".؟!؛»):"


def natural_sort_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def clean_arabic_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    return text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff").strip()


def extract_headings_from_docx(doc_path: str):
    from docx import Document
    doc = Document(doc_path)
    return [p.text.strip() for p in doc.paragraphs if p.text.strip() and
            any(name in (p.style.name if p.style else "").lower()
                for name in ("heading", "عنوان", "تیتر"))]


def _pieces(text, size):
    while len(text) > size:
        candidate = text[:size]
        # Prefer a sentence/word boundary, but always enforce the hard limit.
        cuts = [m.end() for m in re.finditer(r"[.؟!؛]\s+|\s+", candidate)]
        cut = next((i for i in reversed(cuts) if i >= size // 2), size)
        piece = text[:cut].strip()
        if piece:
            yield piece
        text = text[cut:].lstrip()
    if text:
        yield text


def _chunk_paragraphs(paragraphs, chunk_size=1300, user_headings=None, single_chunk=False):
    chunk_size = int(chunk_size)
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    headings = list(user_headings or [])

    if single_chunk:
        clean_paras = [clean_arabic_text(raw) for raw in paragraphs]
        clean_paras = [p for p in clean_paras if p]
        if not clean_paras:
            return []
        full_text = "\n\n".join(clean_paras)
        heading = headings[0] if headings else None
        return [{"idx": 0, "heading": heading, "arabic_text": full_text}]

    headings_set = set(headings)
    chunks, current = [], ""
    heading = None

    def flush():
        nonlocal current
        if current:
            chunks.append({"idx": len(chunks), "heading": heading, "arabic_text": current})
            current = ""

    for raw in paragraphs:
        text = clean_arabic_text(raw)
        if not text:
            continue
        is_heading = text in headings_set or (len(text) < 100 and "\n" not in text and
            re.match(r"^(?:#{1,6}\s*|(?:الفصل|المبحث|المطلب|درس|فصل|باب)(?:\s|$))", text))
        if is_heading:
            flush()
            heading = text.lstrip("#").strip()
        # Keep headings in source text, including consecutive/final heading-only input.
        for piece in _pieces(text, chunk_size):
            if current and len(current) + 2 + len(piece) > chunk_size:
                flush()
            current = current + "\n\n" + piece if current else piece
    flush()
    return chunks


def slice_docx(doc_path: str, chunk_size: int = 1300, user_headings=None, single_chunk: bool = False):
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    doc = Document(doc_path)
    paragraphs, headings = [], list(user_headings or [])
    for block in doc.element.body:
        if block.tag == qn("w:p"):
            paragraph = Paragraph(block, doc)
            paragraphs.append(paragraph.text)
            if any(name in (paragraph.style.name if paragraph.style else "").lower()
                   for name in ("heading", "عنوان", "تیتر")):
                headings.append(paragraph.text)
        elif block.tag == qn("w:tbl"):
            for row in Table(block, doc).rows:
                # Merged cells share the same XML node; include each just once.
                seen, cells = set(), []
                for cell in row.cells:
                    if cell._tc not in seen:
                        seen.add(cell._tc)
                        cells.append(cell.text)
                paragraphs.append(" | ".join(cells))
    return _chunk_paragraphs(paragraphs, chunk_size, headings, single_chunk=single_chunk)


def slice_text_file(text_path: str, chunk_size: int = 1300, single_chunk: bool = False):
    with open(text_path, "r", encoding="utf-8-sig") as stream:
        return slice_raw_text(stream.read(), chunk_size, single_chunk=single_chunk)


def slice_raw_text(raw_text: str, chunk_size: int = 1300, single_chunk: bool = False):
    text = clean_arabic_text(raw_text)
    return _chunk_paragraphs(text.split("\n"), chunk_size, single_chunk=single_chunk)


def build_chunks_from_source(source_path: str, chunk_size: int = 1300, user_headings=None, single_chunk: bool = False):
    if not os.path.exists(source_path):
        raise FileNotFoundError("Input document not found: " + source_path)
    if not os.path.isfile(source_path):
        raise ValueError("Input must be a text, Markdown or Word file (images require OCR)")
    ext = os.path.splitext(source_path)[1].lower()
    if ext == ".docx":
        chunks = slice_docx(source_path, chunk_size, user_headings, single_chunk=single_chunk)
    elif ext in (".txt", ".md", ".markdown"):
        chunks = slice_text_file(source_path, chunk_size, single_chunk=single_chunk)
    else:
        raise ValueError("Unsupported input format; use .txt, .md or .docx")
    if not chunks:
        raise ValueError("Input document contains no usable text")
    return chunks, {"source_path": os.path.abspath(source_path), "chunk_size": chunk_size,
                    "total_chunks": len(chunks), "single_chunk": single_chunk}
