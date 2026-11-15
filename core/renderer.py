"""رندر خروجی نهایی به فایل‌های Word و کتاب‌خوان HTML (برگرفته از Mix jozve).

خروجی‌ها:
۱. فایل Word اصلی (متن عربی + شرح فارسی)
۲. فایل Word تدریس خالص (فقط تدریس فارسی)
۳. کتاب‌خوان HTML تعاملی مدرن با حالت تاریک/روشن و واکنش‌گرا برای گوشی اندروید
۴. کتاب‌خوان HTML تدریس خالص
"""
import html
import os
import re
import tempfile
from pathlib import Path

from .prompt_builder import strip_end_marker

HTML_READER_TEMPLATE = """<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{DOCUMENT_TITLE}</title>
    <style>
        :root {
            --bg-body: #0f172a;
            --bg-card: #1e293b;
            --text-main: #f1f5f9;
            --text-muted: #94a3b8;
            --primary: #38bdf8;
            --accent: #f59e0b;
            --border: #334155;
            --arabic-bg: #1e1b4b;
            --arabic-border: #4338ca;
        }
        @media (prefers-color-scheme: light) {
            :root {
                --bg-body: #f8fafc;
                --bg-card: #ffffff;
                --text-main: #0f172a;
                --text-muted: #64748b;
                --primary: #0284c7;
                --accent: #d97706;
                --border: #e2e8f0;
                --arabic-bg: #f5f3ff;
                --arabic-border: #c4b5fd;
            }
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: system-ui, -apple-system, 'Vazirmatn', 'Segoe UI', Tahoma, sans-serif;
            background-color: var(--bg-body);
            color: var(--text-main);
            line-height: 1.8;
            font-size: 16px;
            padding: 1rem;
            overflow-wrap: anywhere;
        }
        .container {
            max-width: 860px;
            margin: 0 auto;
        }
        header {
            text-align: center;
            padding: 2rem 1rem;
            margin-bottom: 2rem;
            border-bottom: 2px solid var(--border);
        }
        h1 {
            font-size: 1.8rem;
            color: var(--primary);
            margin-bottom: 0.5rem;
        }
        .meta-bar {
            color: var(--text-muted);
            font-size: 0.9rem;
        }
        .chunk-card {
            background-color: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 1.5rem;
            margin-bottom: 1.5rem;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
        }
        .chunk-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border);
            padding-bottom: 0.75rem;
            margin-bottom: 1rem;
        }
        .chunk-num {
            font-weight: bold;
            color: var(--primary);
            font-size: 1.1rem;
        }
        .heading-title {
            color: var(--accent);
            font-weight: 700;
            font-size: 1.2rem;
            margin-bottom: 0.75rem;
        }
        .arabic-box {
            background-color: var(--arabic-bg);
            border-right: 4px solid var(--arabic-border);
            padding: 1rem;
            border-radius: 8px;
            margin-bottom: 1.25rem;
            font-family: 'Amiri', 'Traditional Arabic', serif;
            font-size: 1.15rem;
            line-height: 2;
            white-space: pre-wrap;
            color: #e0e7ff;
        }
        @media (prefers-color-scheme: light) {
            .arabic-box { color: #312e81; }
        }
        .persian-content {
            font-size: 1.05rem;
            line-height: 1.9;
        }
        .persian-content p {
            margin-bottom: 1rem;
        }
        .persian-content strong {
            color: var(--primary);
        }
        .copy-btn {
            background: transparent;
            border: 1px solid var(--border);
            color: var(--text-muted);
            padding: 4px 10px;
            border-radius: 6px;
            font-size: 0.8rem;
            cursor: pointer;
        }
        .copy-btn:hover {
            color: var(--text-main);
            border-color: var(--primary);
        }
        ul, ol { padding-inline-start: 1.5rem; margin-bottom: 1rem; }
        .copy-btn { min-height: 44px; }
        .chunk-header { gap: 12px; flex-wrap: wrap; }
        @media (max-width: 480px) { .chunk-card { padding: 1rem; } }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>{DOCUMENT_TITLE}</h1>
            <div class="meta-bar">تولید شده با جزوه‌ساز هوشمند اندروید (موتور Mix API)</div>
        </header>
        <main>
            {CONTENT_BLOCKS}
        </main>
    </div>
<script>
document.querySelectorAll('[data-copy]').forEach(button => button.addEventListener('click', async () => {
    const content = document.getElementById(button.dataset.copy);
    try {
        if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(content.innerText);
        } else {
            const range = document.createRange();
            range.selectNodeContents(content);
            const selection = window.getSelection();
            selection.removeAllRanges(); selection.addRange(range);
            if (!document.execCommand('copy')) throw new Error('copy');
            selection.removeAllRanges();
        }
        button.textContent = 'کپی شد';
    } catch (_) { button.textContent = 'متن را انتخاب و کپی کنید'; }
}));
</script>
</body>
</html>
"""


def _inline_html(value: str) -> str:
    safe = html.escape(value)
    safe = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe)
    return re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", safe)


def markdown_to_html_paragraphs(md_text: str) -> str:
    """Render a safe, deliberately small Markdown subset (never raw HTML)."""
    output = []
    list_type = None
    for line in (md_text or "").splitlines():
        line = line.strip()
        bullet = re.match(r"^[-*]\s+(.+)$", line)
        ordered = re.match(r"^\d+[.)]\s+(.+)$", line)
        wanted = "ul" if bullet else "ol" if ordered else None
        if list_type != wanted:
            if list_type:
                output.append(f"</{list_type}>")
            if wanted:
                output.append(f"<{wanted}>")
            list_type = wanted
        if wanted:
            output.append(f"<li>{_inline_html((bullet or ordered).group(1))}</li>")
        elif line:
            heading = re.match(r"^(#{1,6})\s+(.+)$", line)
            if heading:
                level = min(len(heading.group(1)) + 1, 6)
                output.append(f"<h{level}>{_inline_html(heading.group(2))}</h{level}>")
            else:
                output.append(f"<p>{_inline_html(line)}</p>")
    if list_type:
        output.append(f"</{list_type}>")
    return "\n".join(output)


def _completed_chunks(chunks):
    """Failed/claimed partial responses must never masquerade as final teaching."""
    result = []
    seen = set()
    for chunk in chunks:
        if chunk.get("state") not in ("done", "completed"):
            continue
        idx = chunk.get("idx")
        if isinstance(idx, bool) or not isinstance(idx, int) or idx < 0 or idx in seen:
            raise ValueError("Completed chunks require unique nonnegative integer indices")
        seen.add(idx)
        if not strip_end_marker(chunk.get("persian_text") or ""):
            continue
        result.append(chunk)
    return sorted(result, key=lambda chunk: chunk["idx"])


def _atomic_write(output_path, writer):
    """A failed save leaves any existing artifact intact, including on Windows."""
    target = Path(output_path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".jozve-render-", suffix=target.suffix, dir=target.parent)
    os.close(fd)
    try:
        writer(temporary)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def render_html_document(chunks: list, project_title: str, pure: bool, output_path: str):
    blocks = []
    for chunk in _completed_chunks(chunks):
        idx = chunk["idx"] + 1
        heading = chunk.get("heading") or ""
        heading_html = f'<h2 class="heading-title">{html.escape(heading)}</h2>' if heading else ""
        arabic = chunk.get("arabic_text") or ""
        arabic_box = ""
        if arabic and not pure:
            arabic_box = f'<div class="arabic-box"><strong>متن عبارت:</strong><br>{html.escape(arabic)}</div>'
        persian_html = markdown_to_html_paragraphs(strip_end_marker(chunk.get("persian_text") or ""))
        blocks.append(f"""
        <article class="chunk-card" id="chunk-{idx}">
            <div class="chunk-header">
                <span class="chunk-num">قطعه شماره {idx}</span>
                <button class="copy-btn" type="button" data-copy="text-{idx}">کپی متن</button>
            </div>
            {heading_html}
            {arabic_box}
            <div class="persian-content" id="text-{idx}">{persian_html}</div>
        </article>""")
    title = str(project_title) + (" (تدریس خالص)" if pure else "")
    # One-pass substitution: literal placeholder text in a title must remain text.
    replacements = {"DOCUMENT_TITLE": html.escape(title), "CONTENT_BLOCKS": "\n".join(blocks)}
    rendered = re.sub(r"\{(DOCUMENT_TITLE|CONTENT_BLOCKS)\}", lambda match: replacements[match.group(1)], HTML_READER_TEMPLATE)
    _atomic_write(output_path, lambda path: Path(path).write_text(rendered, encoding="utf-8"))


def render_docx_document(chunks: list, project_title: str, pure: bool, output_path: str):
    # Import/save errors are intentionally propagated so the API cannot claim success.
    from docx import Document
    from docx.shared import Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    def rtl(paragraph):
        properties = paragraph._p.get_or_add_pPr()
        bidi = OxmlElement("w:bidi")
        bidi.set(qn("w:val"), "1")
        properties.append(bidi)
        paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        return paragraph

    def add_runs(paragraph, value):
        for part in re.split(r"(\*\*.+?\*\*|(?<!\*)\*[^*]+\*(?!\*))", value):
            bold = part.startswith("**") and part.endswith("**") and len(part) > 4
            italic = not bold and part.startswith("*") and part.endswith("*") and len(part) > 2
            run = paragraph.add_run(part[2:-2] if bold else part[1:-1] if italic else part)
            run.bold = bold
            run.italic = italic

    doc = Document()
    normal = doc.styles["Normal"]
    normal.font.name = "Arial"
    normal.font.size = Pt(12)
    title = rtl(doc.add_paragraph(style="Title"))
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.add_run(str(project_title) + (" - تدریس خالص" if pure else ""))
    for chunk in _completed_chunks(chunks):
        idx = chunk["idx"] + 1
        if chunk.get("heading"):
            rtl(doc.add_paragraph(str(chunk["heading"]), style="Heading 1"))
        if not pure and chunk.get("arabic_text"):
            rtl(doc.add_paragraph(f"قطعه {idx} (متن عبارت):", style="Heading 2"))
            rtl(doc.add_paragraph(chunk["arabic_text"]))
        rtl(doc.add_paragraph(f"تدریس و شرح قطعه {idx}:", style="Heading 2"))
        for line in strip_end_marker(chunk.get("persian_text") or "").splitlines():
            line = line.strip()
            if not line:
                continue
            heading = re.match(r"^(#{1,6})\s+(.+)$", line)
            bullet = re.match(r"^[-*]\s+(.+)$", line)
            ordered = re.match(r"^\d+[.)]\s+(.+)$", line)
            if heading:
                paragraph = rtl(doc.add_paragraph(style=f"Heading {min(len(heading.group(1)) + 2, 9)}"))
                content = heading.group(2)
            elif bullet or ordered:
                paragraph = rtl(doc.add_paragraph(style="List Bullet" if bullet else "List Number"))
                content = (bullet or ordered).group(1)
            else:
                paragraph = rtl(doc.add_paragraph())
                content = line
            add_runs(paragraph, content)
    _atomic_write(output_path, doc.save)


def render_all_outputs(chunks: list, project_title: str, output_base: str):
    """Stage all four outputs before replacing any previous successful artifacts."""
    chunks = _completed_chunks(chunks)
    if not chunks:
        raise ValueError("No completed teaching chunks to render")
    paths = {
        "html": f"{output_base}.html",
        "khales_html": f"{output_base}_تدریس_خالص.html",
        "docx": f"{output_base}.docx",
        "khales_docx": f"{output_base}_تدریس_خالص.docx",
    }
    parent = Path(output_base).resolve().parent
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".jozve-render-", dir=parent) as staging:
        for key, path in paths.items():
            staged = Path(staging) / Path(path).name
            render = render_docx_document if key.endswith("docx") else render_html_document
            render(chunks, project_title, pure=key.startswith("khales"), output_path=str(staged))
        for path in paths.values():
            os.replace(Path(staging) / Path(path).name, path)
    return paths
