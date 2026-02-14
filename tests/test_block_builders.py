"""Unit tests for text_to_blocks() — no Notion credentials needed."""

from __future__ import annotations

import pytest

from ultimate_brain_mcp.formatters import blocks_to_text, text_to_blocks


class TestEmptyInput:
    def test_none_returns_empty(self):
        assert text_to_blocks("") == []

    def test_whitespace_returns_empty(self):
        assert text_to_blocks("   \n\n  ") == []


class TestParagraphs:
    def test_single_paragraph(self):
        blocks = text_to_blocks("Hello world")
        assert len(blocks) == 1
        assert blocks[0]["type"] == "paragraph"
        assert blocks[0]["paragraph"]["rich_text"][0]["text"]["content"] == "Hello world"

    def test_consecutive_lines_joined(self):
        blocks = text_to_blocks("Line one\nLine two\nLine three")
        assert len(blocks) == 1
        assert blocks[0]["type"] == "paragraph"
        content = blocks[0]["paragraph"]["rich_text"][0]["text"]["content"]
        assert "Line one" in content
        assert "Line two" in content
        assert "Line three" in content

    def test_blank_line_splits_paragraphs(self):
        blocks = text_to_blocks("Para one\n\nPara two")
        assert len(blocks) == 2
        assert blocks[0]["type"] == "paragraph"
        assert blocks[1]["type"] == "paragraph"


class TestHeadings:
    def test_heading_1(self):
        blocks = text_to_blocks("# Main Title")
        assert len(blocks) == 1
        assert blocks[0]["type"] == "heading_1"
        assert blocks[0]["heading_1"]["rich_text"][0]["text"]["content"] == "Main Title"

    def test_heading_2(self):
        blocks = text_to_blocks("## Section")
        assert len(blocks) == 1
        assert blocks[0]["type"] == "heading_2"
        assert blocks[0]["heading_2"]["rich_text"][0]["text"]["content"] == "Section"

    def test_heading_3(self):
        blocks = text_to_blocks("### Sub-section")
        assert len(blocks) == 1
        assert blocks[0]["type"] == "heading_3"
        assert blocks[0]["heading_3"]["rich_text"][0]["text"]["content"] == "Sub-section"


class TestBullets:
    def test_single_bullet(self):
        blocks = text_to_blocks("- Item one")
        assert len(blocks) == 1
        assert blocks[0]["type"] == "bulleted_list_item"
        assert blocks[0]["bulleted_list_item"]["rich_text"][0]["text"]["content"] == "Item one"

    def test_multiple_bullets(self):
        blocks = text_to_blocks("- A\n- B\n- C")
        assert len(blocks) == 3
        assert all(b["type"] == "bulleted_list_item" for b in blocks)


class TestNumberedList:
    def test_single_numbered(self):
        blocks = text_to_blocks("1. First")
        assert len(blocks) == 1
        assert blocks[0]["type"] == "numbered_list_item"
        assert blocks[0]["numbered_list_item"]["rich_text"][0]["text"]["content"] == "First"

    def test_multi_digit_numbers(self):
        blocks = text_to_blocks("10. Tenth item")
        assert len(blocks) == 1
        assert blocks[0]["type"] == "numbered_list_item"
        assert blocks[0]["numbered_list_item"]["rich_text"][0]["text"]["content"] == "Tenth item"


class TestToDo:
    def test_unchecked(self):
        blocks = text_to_blocks("- [ ] Buy milk")
        assert len(blocks) == 1
        assert blocks[0]["type"] == "to_do"
        assert blocks[0]["to_do"]["checked"] is False
        assert blocks[0]["to_do"]["rich_text"][0]["text"]["content"] == "Buy milk"

    def test_checked(self):
        blocks = text_to_blocks("- [x] Done task")
        assert len(blocks) == 1
        assert blocks[0]["type"] == "to_do"
        assert blocks[0]["to_do"]["checked"] is True

    def test_checked_uppercase(self):
        blocks = text_to_blocks("- [X] Also done")
        assert len(blocks) == 1
        assert blocks[0]["to_do"]["checked"] is True


class TestCodeBlock:
    def test_code_with_language(self):
        blocks = text_to_blocks("```python\nprint('hello')\n```")
        assert len(blocks) == 1
        assert blocks[0]["type"] == "code"
        assert blocks[0]["code"]["language"] == "python"
        assert blocks[0]["code"]["rich_text"][0]["text"]["content"] == "print('hello')"

    def test_code_no_language(self):
        blocks = text_to_blocks("```\nsome code\n```")
        assert len(blocks) == 1
        assert blocks[0]["type"] == "code"
        assert blocks[0]["code"]["language"] == "plain text"

    def test_multiline_code(self):
        blocks = text_to_blocks("```js\nconst a = 1;\nconst b = 2;\n```")
        assert len(blocks) == 1
        content = blocks[0]["code"]["rich_text"][0]["text"]["content"]
        assert "const a = 1;" in content
        assert "const b = 2;" in content


class TestQuote:
    def test_single_quote(self):
        blocks = text_to_blocks("> This is a quote")
        assert len(blocks) == 1
        assert blocks[0]["type"] == "quote"
        assert blocks[0]["quote"]["rich_text"][0]["text"]["content"] == "This is a quote"


class TestDivider:
    def test_divider(self):
        blocks = text_to_blocks("---")
        assert len(blocks) == 1
        assert blocks[0]["type"] == "divider"


class TestMixedContent:
    def test_full_document(self):
        doc = """# Meeting Notes

This is the introduction paragraph.

## Action Items

- [ ] Review the proposal
- [x] Send invites
- Follow up with team

### Code Example

```python
def hello():
    return "world"
```

> Important: deadline is Friday

---

Final thoughts here."""
        blocks = text_to_blocks(doc)

        types = [b["type"] for b in blocks]
        assert types[0] == "heading_1"
        assert "paragraph" in types
        assert types[2] == "heading_2"
        assert "to_do" in types
        assert "bulleted_list_item" in types
        assert types[6] == "heading_3"
        assert "code" in types
        assert "quote" in types
        assert "divider" in types


class TestLongTextChunking:
    def test_long_text_is_chunked(self):
        long_text = "A" * 5000
        blocks = text_to_blocks(long_text)
        assert len(blocks) == 1
        rich_text = blocks[0]["paragraph"]["rich_text"]
        assert len(rich_text) == 3  # 2000 + 2000 + 1000
        assert len(rich_text[0]["text"]["content"]) == 2000
        assert len(rich_text[1]["text"]["content"]) == 2000
        assert len(rich_text[2]["text"]["content"]) == 1000


class TestRoundTrip:
    """Verify that text_to_blocks → blocks_to_text preserves content (not exact format)."""

    def test_headings_round_trip(self):
        original = "# Title\n## Section\n### Sub"
        blocks = text_to_blocks(original)
        result = blocks_to_text(blocks)
        assert "# Title" in result
        assert "## Section" in result
        assert "### Sub" in result

    def test_bullets_round_trip(self):
        original = "- Alpha\n- Beta"
        blocks = text_to_blocks(original)
        result = blocks_to_text(blocks)
        assert "Alpha" in result
        assert "Beta" in result

    def test_todo_round_trip(self):
        original = "- [ ] Pending\n- [x] Done"
        blocks = text_to_blocks(original)
        result = blocks_to_text(blocks)
        assert "[ ] Pending" in result
        assert "[x] Done" in result

    def test_code_round_trip(self):
        original = "```python\nprint('hi')\n```"
        blocks = text_to_blocks(original)
        result = blocks_to_text(blocks)
        assert "python" in result
        assert "print('hi')" in result

    def test_divider_round_trip(self):
        blocks = text_to_blocks("---")
        result = blocks_to_text(blocks)
        assert "---" in result


class TestBlockObjectField:
    """All blocks should have object='block'."""

    def test_all_blocks_have_object_field(self):
        doc = "# H\n- bullet\n1. num\n- [ ] todo\n> quote\n---\nplain"
        blocks = text_to_blocks(doc)
        for block in blocks:
            assert block.get("object") == "block", f"Missing object field on {block['type']}"
