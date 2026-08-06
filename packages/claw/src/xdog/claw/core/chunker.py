import re


class BlockChunker:
    """Paragraph-aware message splitting."""

    def __init__(self, *, min_chars: int = 100, max_chars: int = 2000):
        self.min_chars = min_chars
        self.max_chars = max_chars

    def chunk(self, text: str) -> list[str]:
        """Split preserving code fences and paragraphs."""
        if not text or not text.strip():
            return []

        if len(text) <= self.max_chars:
            return [text]

        # First pass: split text preserving code blocks
        blocks = []
        # Match code blocks: ```...```, non-greedy match across lines
        code_fence_pattern = re.compile(r'(```.*?```)', re.DOTALL)
        parts = code_fence_pattern.split(text)

        for part in parts:
            if not part:
                continue
            if part.startswith('```') and part.endswith('```'):
                # Handle long code blocks
                if len(part) > self.max_chars:
                    # Very simple hard split for long code blocks, keeping tags
                    # This might break syntax but is a fallback for huge blocks
                    # Better logic might involve line splitting
                    lines = part.splitlines(keepends=True)
                    current_block: list[str] = []
                    current_length = 0

                    for line in lines:
                        if current_length + len(line) > self.max_chars and current_block:
                            # if not the first block, add ending ```
                            if current_block[0] != '```\n' and not current_block[0].startswith('```'):
                                current_block.insert(0, '```\n')
                            if not line.startswith('```') and not current_block[-1].endswith('```\n'):
                                current_block.append('```\n')

                            blocks.append("".join(current_block))
                            current_block = ['```\n', line] if not line.startswith('```') else [line]
                            current_length = len(current_block[0]) + len(line)
                        else:
                            current_block.append(line)
                            current_length += len(line)

                    if current_block:
                        if not current_block[-1].strip() == '```':
                            current_block.append('```\n')
                        blocks.append("".join(current_block))
                else:
                    blocks.append(part)
            else:
                # Regular text part: split by paragraph boundaries
                paragraphs = re.split(r'(\n\n+)', part)

                current_paragraph: list[str] = []
                for p in paragraphs:
                    if not p:
                        continue
                    if re.match(r'^\n\n+$', p):
                        # Add separator to previous paragraph if any, or next
                        if current_paragraph:
                            current_paragraph.append(p)
                        else:
                            blocks.append(p)  # rare case, standalone newlines
                    else:
                        if current_paragraph:
                            p_text = "".join(current_paragraph) + p
                        else:
                            p_text = p

                        # If the paragraph itself is longer than max_chars, hard split it
                        if len(p_text) > self.max_chars:
                            # Split into chunks of max_chars
                            for i in range(0, len(p_text), self.max_chars):
                                blocks.append(p_text[i:i+self.max_chars])
                        else:
                            blocks.append(p_text)
                        current_paragraph = []

                if current_paragraph:
                    blocks.append("".join(current_paragraph))

        # Second pass: Merge small consecutive blocks up to max_chars
        merged_chunks = []
        current_chunk = []
        current_len = 0

        for block in blocks:
            if current_len + len(block) <= self.max_chars:
                current_chunk.append(block)
                current_len += len(block)
            else:
                if current_chunk:
                    merged_chunks.append("".join(current_chunk))
                # The block itself might still be > max_chars if it's a huge code block
                # (handled above, but just in case)
                if len(block) > self.max_chars:
                    # Hard split
                    for i in range(0, len(block), self.max_chars):
                        merged_chunks.append(block[i:i+self.max_chars])
                else:
                    current_chunk = [block]
                    current_len = len(block)

        if current_chunk:
            merged_chunks.append("".join(current_chunk))

        return merged_chunks
