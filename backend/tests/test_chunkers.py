from app.rag import chunkers
from app.rag.chunkers import chunk_incident, chunk_issue, chunk_markdown, chunk_python

DOC = """# Database

Intro line.

## Connection pooling

Each instance keeps its own pool:

```
# not a heading, just a comment in a code block
total = DB_POOL_SIZE * INSTANCES
```

The total must stay at or below max_connections. Recommended pool size is 20.

## Schema

Tables live in migrations.py.
"""


def test_markdown_chunks_follow_headings():
    chunks = chunk_markdown(DOC)
    assert [c.section_path for c in chunks] == [
        "Database",
        "Database > Connection pooling",
        "Database > Schema",
    ]


def test_rule_stays_in_one_chunk_with_its_context():
    pooling = chunk_markdown(DOC)[1]
    assert "DB_POOL_SIZE * INSTANCES" in pooling.content
    assert "Recommended pool size is 20" in pooling.content


def test_hash_inside_code_block_is_not_a_heading():
    assert len(chunk_markdown(DOC)) == 3


def test_markdown_chunks_keep_line_numbers():
    pooling = chunk_markdown(DOC)[1]
    lines = DOC.splitlines()
    assert lines[pooling.start_line - 1] == "## Connection pooling"
    assert "Recommended pool size" in lines[pooling.end_line - 1]


def test_long_section_splits_at_paragraphs_never_mid_paragraph(monkeypatch):
    monkeypatch.setattr(chunkers, "MAX_CHARS", 60)
    text = "# Long\n\n" + "\n\n".join(f"Paragraph {i} has some words in it." for i in range(5))
    chunks = chunk_markdown(text)
    assert len(chunks) > 1
    for chunk in chunks:
        for paragraph in chunk.content.split("\n\n"):
            assert paragraph.startswith(("# Long", "Paragraph")) and paragraph.endswith(
                (".", "Long")
            )


CODE = '''"""Payments."""
from decimal import Decimal

CENT = Decimal("0.01")


def calculate_total(items):
    total = sum(Decimal(i["price"]) for i in items)
    return total.quantize(CENT)


@dataclass
class Charge:
    id: str
'''


def test_python_chunks_per_function_and_class():
    chunks = chunk_python(CODE)
    assert [c.symbol for c in chunks] == ["<module>", "calculate_total", "Charge"]


def test_python_chunk_includes_decorator_and_whole_body():
    charge = chunk_python(CODE)[2]
    assert charge.content.startswith("@dataclass")
    assert charge.content.endswith("id: str")


def test_module_chunk_holds_imports_and_constants():
    module = chunk_python(CODE)[0]
    assert "from decimal import Decimal" in module.content
    assert 'CENT = Decimal("0.01")' in module.content
    assert "def calculate_total" not in module.content


def test_broken_python_falls_back_to_one_chunk():
    assert len(chunk_python("def broken(:\n    pass")) == 1


def test_incident_and_issue_become_one_chunk_each():
    inc = chunk_incident(
        {"key": "INC-017", "title": "Pool", "symptoms": "500s", "root_cause": "pool 60"}
    )
    assert inc.section_path == "INC-017" and "Root cause: pool 60" in inc.content
    issue = chunk_issue({"number": 57, "title": "500s", "body": "after deploy"})
    assert issue.section_path == "issue:57" and "after deploy" in issue.content
