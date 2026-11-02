"""Parse policy documents (PDF, DOCX, TXT, Markdown) into titled sections (FR-07).

Uploaded files are untrusted: size and type are checked before parsing, and the text is only ever stored and
returned as data.
"""

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import PurePath

MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
MAX_DOCX_UNCOMPRESSED = 100 * 1024 * 1024  # zip-bomb guard
MIME_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
}
_MARKDOWN_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
# "3", "3.2", "3.2.1" followed by a title; also "Section 4: Title".
_NUMBERED_HEADING = re.compile(r"^(?:section\s+)?(\d+(?:\.\d+){0,3})[.):]?\s+([A-Z][^\n]{1,100})$", re.IGNORECASE)


class DocumentRejected(ValueError):
    pass


@dataclass(frozen=True)
class Section:
    path: tuple[str, ...]  # heading trail, e.g. ("Fee Policy", "3 Disputes", "3.2 Thresholds")
    text: str
    page: int | None = None  # first page, PDFs only


@dataclass(frozen=True)
class ParsedDocument:
    title: str
    mime_type: str
    sections: list[Section]


def parse_document(file_name: str, content: bytes) -> ParsedDocument:
    suffix = PurePath(file_name).suffix.lower()
    if suffix not in MIME_TYPES:
        raise DocumentRejected(f"unsupported file type {suffix or '(none)'}; allowed: {sorted(MIME_TYPES)}")
    if not content:
        raise DocumentRejected("file is empty")
    if len(content) > MAX_DOCUMENT_BYTES:
        raise DocumentRejected(f"file exceeds {MAX_DOCUMENT_BYTES // (1024 * 1024)} MB")

    if suffix == ".pdf":
        sections = _parse_pdf(content)
    elif suffix == ".docx":
        sections = _parse_docx(content)
    else:
        text = _decode(content)
        sections = _parse_markdown(text) if suffix in (".md", ".markdown") else _parse_plain(text)

    sections = [s for s in sections if s.text.strip()]
    if not sections:
        raise DocumentRejected("no readable text found")
    title = sections[0].path[0] if sections[0].path else PurePath(file_name).stem.replace("_", " ")
    return ParsedDocument(title=title.strip(), mime_type=MIME_TYPES[suffix], sections=sections)


# --- formats ---


def _parse_markdown(text: str) -> list[Section]:
    builder = _SectionBuilder()
    for line in text.splitlines():
        match = _MARKDOWN_HEADING.match(line)
        if match:
            builder.heading(len(match.group(1)), match.group(2).strip())
        else:
            builder.line(line)
    return builder.finish()


def _parse_plain(text: str) -> list[Section]:
    builder = _SectionBuilder()
    lines = text.splitlines()
    first = next((i for i, line in enumerate(lines) if line.strip()), None)
    for i, line in enumerate(lines):
        stripped = line.strip()
        numbered = _NUMBERED_HEADING.match(stripped)
        if i == first and not numbered:
            builder.heading(1, stripped)  # first non-empty line is the document title
        elif numbered:
            builder.heading(2 + numbered.group(1).count("."), stripped)
        else:
            builder.line(line)
    return builder.finish()


def _parse_docx(content: bytes) -> list[Section]:
    from docx import Document

    if not content.startswith(b"PK\x03\x04"):
        raise DocumentRejected("not a valid DOCX file")
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if sum(info.file_size for info in archive.infolist()) > MAX_DOCX_UNCOMPRESSED:
                raise DocumentRejected("DOCX expands to an unreasonable size")
        document = Document(io.BytesIO(content))
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        if isinstance(exc, DocumentRejected):
            raise
        raise DocumentRejected(f"could not read DOCX: {exc}") from None

    builder = _SectionBuilder()
    for paragraph in document.paragraphs:
        style = (paragraph.style.name if paragraph.style is not None else "") or ""
        text = paragraph.text.strip()
        if style == "Title" and text:
            builder.heading(1, text)
        elif style.startswith("Heading") and text:
            level = int(style.split()[-1]) if style.split()[-1].isdigit() else 1
            builder.heading(level + 1, text)
        else:
            builder.line(paragraph.text)
    for table in document.tables:  # tables carry thresholds and limits in many policies
        for row in table.rows:
            builder.line(" | ".join(cell.text.strip() for cell in row.cells))
    return builder.finish()


def _parse_pdf(content: bytes) -> list[Section]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    if not content.startswith(b"%PDF"):
        raise DocumentRejected("not a valid PDF file")
    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            raise DocumentRejected("encrypted PDFs are not supported")
        pages = [page.extract_text() or "" for page in reader.pages]
    except PdfReadError as exc:
        raise DocumentRejected(f"could not read PDF: {exc}") from None

    builder = _SectionBuilder()
    first_line_seen = False
    for number, page_text in enumerate(pages, start=1):
        builder.page = number
        for line in page_text.splitlines():
            stripped = line.strip()
            numbered = _NUMBERED_HEADING.match(stripped)
            if not first_line_seen and stripped:
                first_line_seen = True
                if not numbered:
                    builder.heading(1, stripped)
                    continue
            if numbered:
                builder.heading(2 + numbered.group(1).count("."), stripped)
            else:
                builder.line(line)
    return builder.finish()


# --- helpers ---


class _SectionBuilder:
    """Accumulates lines under the current heading trail."""

    def __init__(self) -> None:
        self.trail: list[tuple[int, str]] = []
        self.lines: list[str] = []
        self.sections: list[Section] = []
        self.page: int | None = None
        self.section_page: int | None = None

    def heading(self, level: int, title: str) -> None:
        self._flush()
        self.trail = [(lvl, t) for lvl, t in self.trail if lvl < level] + [(level, title)]
        self.section_page = self.page

    def line(self, text: str) -> None:
        if not self.lines and not text.strip():
            return
        if not self.lines:
            self.section_page = self.section_page or self.page
        self.lines.append(text.rstrip())

    def finish(self) -> list[Section]:
        self._flush()
        return self.sections

    def _flush(self) -> None:
        text = "\n".join(self.lines).strip()
        if text:
            self.sections.append(Section(path=tuple(t for _, t in self.trail), text=text, page=self.section_page))
        self.lines = []


def _decode(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise DocumentRejected("text file is not valid UTF-8") from None
