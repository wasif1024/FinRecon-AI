"""Split sections into retrieval-sized chunks. Chunks never cross a section boundary, so every chunk has one
section to cite."""

from dataclasses import dataclass

from finrecon_ai.policies.parsing import Section

MAX_CHARS = 1200
OVERLAP_CHARS = 150


@dataclass(frozen=True)
class Chunk:
    index: int
    section_path: tuple[str, ...]
    page: int | None
    text: str

    @property
    def section(self) -> str:
        return " › ".join(self.section_path)

    @property
    def embedding_text(self) -> str:
        """The heading trail is embedded with the text so a chunk is found by its section's topic too."""
        return f"{self.section}\n\n{self.text}" if self.section_path else self.text


def chunk_sections(sections: list[Section], max_chars: int = MAX_CHARS, overlap: int = OVERLAP_CHARS) -> list[Chunk]:
    chunks: list[Chunk] = []
    for section in sections:
        for piece in _split(section.text, max_chars, overlap):
            chunks.append(Chunk(index=len(chunks), section_path=section.path, page=section.page, text=piece))
    return chunks


def _split(text: str, max_chars: int, overlap: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    # Prefer paragraph boundaries; fall back to hard splits for very long paragraphs.
    pieces: list[str] = []
    current = ""
    for paragraph in [p.strip() for p in text.split("\n\n") if p.strip()]:
        while len(paragraph) > max_chars:
            if current:
                pieces.append(current)
                current = ""
            cut = paragraph.rfind(" ", 0, max_chars)
            cut = cut if cut > max_chars // 2 else max_chars
            pieces.append(paragraph[:cut].strip())
            paragraph = paragraph[max(cut - overlap, 0):].strip()
        if current and len(current) + 2 + len(paragraph) > max_chars:
            pieces.append(current)
            tail = current[-overlap:]
            current = (tail[tail.find(" ") + 1:] + "\n\n" if " " in tail else "") + paragraph
        else:
            current = f"{current}\n\n{paragraph}" if current else paragraph
    if current:
        pieces.append(current)
    return pieces
