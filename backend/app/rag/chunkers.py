"""Cut sources into chunks along their own structure, never at a fixed size.

- Markdown: one chunk per section (heading), keeping the heading path, e.g.
  "Database > Connection pooling". Long sections split at blank lines, never mid-paragraph.
- Python: one chunk per top-level function or class (with its decorators), plus one chunk
  for module-level code such as imports and constants.
- Incidents and issues: one chunk per record.

Every chunk keeps its line numbers so evidence can cite `file:start-end`.
"""

import ast
import re
from dataclasses import dataclass

MAX_CHARS = 2000  # about 500 tokens; longer sections are split at paragraph breaks

HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


@dataclass
class Chunk:
    content: str
    start_line: int | None = None
    end_line: int | None = None
    section_path: str | None = None
    symbol: str | None = None


def chunk_markdown(text: str) -> list[Chunk]:
    lines = text.splitlines()
    chunks: list[Chunk] = []
    headings: list[tuple[int, str]] = []  # (level, title) of the current section and its parents
    section: list[tuple[int, str]] = []  # (line number, text) of the current section
    in_code_fence = False

    def flush() -> None:
        path = " > ".join(title for _, title in headings) or None
        chunks.extend(_split_long(section, path))
        section.clear()

    for number, line in enumerate(lines, start=1):
        if line.lstrip().startswith("```"):
            in_code_fence = not in_code_fence
        match = None if in_code_fence else HEADING.match(line)
        if match:
            flush()
            level = len(match[1])
            headings = [h for h in headings if h[0] < level] + [(level, match[2])]
        section.append((number, line))
    flush()
    return chunks


def _split_long(lines: list[tuple[int, str]], path: str | None) -> list[Chunk]:
    """Group lines into chunks under MAX_CHARS, breaking only at blank lines."""
    paragraphs: list[list[tuple[int, str]]] = [[]]
    for number, line in lines:
        if line.strip():
            paragraphs[-1].append((number, line))
        elif paragraphs[-1]:
            paragraphs.append([])

    chunks: list[Chunk] = []
    current: list[tuple[int, str]] = []
    for para in filter(None, paragraphs):
        size = sum(len(text) + 1 for _, text in current + para)
        if current and size > MAX_CHARS:
            chunks.append(_make_chunk(current, path))
            current = []
        current += para + [(para[-1][0], "")]  # keep a blank line between paragraphs
    if current:
        chunks.append(_make_chunk(current, path))
    return chunks


def _make_chunk(lines: list[tuple[int, str]], path: str | None) -> Chunk:
    return Chunk(
        content="\n".join(text for _, text in lines).strip(),
        start_line=lines[0][0],
        end_line=lines[-1][0],
        section_path=path,
    )


def chunk_python(source: str) -> list[Chunk]:
    """One chunk per top-level def/class; everything else goes into one module chunk."""
    lines = source.splitlines()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return [Chunk(content=source, start_line=1, end_line=len(lines), symbol="<module>")]

    chunks: list[Chunk] = []
    covered: set[int] = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            # ponytail: a large class stays one chunk; split per method if classes outgrow MAX_CHARS.
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
            end = node.end_lineno or node.lineno
            chunks.append(Chunk("\n".join(lines[start - 1 : end]), start, end, symbol=node.name))
            covered.update(range(start, end + 1))

    rest = [(n, line) for n, line in enumerate(lines, start=1) if n not in covered and line.strip()]
    if rest:
        chunks.insert(
            0,
            Chunk(
                content="\n".join(line for _, line in rest),
                start_line=rest[0][0],
                end_line=rest[-1][0],
                symbol="<module>",
            ),
        )
    return chunks


def chunk_incident(record: dict) -> Chunk:
    parts = [
        f"{record['key']} · {record.get('severity', '')} · {record.get('date', '')}",
        record["title"],
        f"Symptoms: {record.get('symptoms', '')}",
        f"Root cause: {record.get('root_cause', '')}",
        f"Fix: {record.get('fix', '')}",
    ]
    return Chunk(content="\n".join(parts), section_path=record["key"])


def chunk_issue(record: dict) -> Chunk:
    header = f"Issue #{record['number']} ({record.get('state', 'open')}, {record.get('created_at', '')})"
    return Chunk(
        content=f"{header}\n{record['title']}\n{record.get('body', '')}",
        section_path=f"issue:{record['number']}",
    )
