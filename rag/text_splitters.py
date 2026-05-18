"""
Native text splitters

Replaces langchain_text_splitters.RecursiveCharacterTextSplitter
and langchain_text_splitters.MarkdownHeaderTextSplitter
with self-contained implementations.
"""

import re
from typing import Any, Dict, List, Optional, Tuple

from agent.runtime.contracts.messages import Document


class RecursiveCharacterTextSplitter:
    """Split text recursively by a list of separators.

    Tries each separator in order, splitting the text into chunks
    that fit within chunk_size while preserving chunk_overlap
    between adjacent chunks for context continuity.
    """

    def __init__(
        self,
        chunk_size: int = 300,
        chunk_overlap: int = 100,
        separators: Optional[List[str]] = None,
        keep_separator: bool = True,
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.separators = separators or ["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""]
        self.keep_separator = keep_separator

    def split_text(self, text: str) -> List[str]:
        if not text or not text.strip():
            return []
        if len(text) <= self.chunk_size:
            return [text]

        return self._recursive_split(text, self.separators)

    def _recursive_split(self, text: str, separators: List[str]) -> List[str]:
        if not separators:
            return self._split_by_chars(text)

        separator = separators[0]
        remaining_separators = separators[1:]

        if separator == "":
            return self._split_by_chars(text)

        splits = text.split(separator)

        if self.keep_separator:
            merged_splits = []
            for i, s in enumerate(splits):
                if i > 0:
                    merged_splits.append(separator + s)
                else:
                    merged_splits.append(s)
            splits = merged_splits

        good_splits: List[str] = []
        current_chunk: List[str] = []
        for s in splits:
            if len(s) + sum(len(c) for c in current_chunk) <= self.chunk_size:
                current_chunk.append(s)
            else:
                if current_chunk:
                    merged = "".join(current_chunk)
                    if merged.strip():
                        good_splits.append(merged)
                    current_chunk = []

                if len(s) <= self.chunk_size:
                    current_chunk = [s]
                else:
                    sub_splits = self._recursive_split(s, remaining_separators)
                    good_splits.extend(sub_splits)

        if current_chunk:
            merged = "".join(current_chunk)
            if merged.strip():
                good_splits.append(merged)

        return self._merge_splits_with_overlap(good_splits)

    def _split_by_chars(self, text: str) -> List[str]:
        chunks = []
        start = 0
        while start < len(text):
            end = min(start + self.chunk_size, len(text))
            chunk = text[start:end]
            if chunk.strip():
                chunks.append(chunk)
            start = end - self.chunk_overlap if end < len(text) else end
        return chunks

    def _merge_splits_with_overlap(self, splits: List[str]) -> List[str]:
        if not splits:
            return []

        merged = []
        current = splits[0]
        for i in range(1, len(splits)):
            overlap_text = current[-self.chunk_overlap:] if len(current) > self.chunk_overlap else current
            candidate = overlap_text + splits[i]
            if len(candidate) <= self.chunk_size:
                current = candidate
            else:
                merged.append(current)
                current = splits[i]
        merged.append(current)
        return merged


class MarkdownHeaderTextSplitter:
    """Split markdown text by header levels, preserving header metadata.

    Each chunk carries metadata indicating which headers it belongs to.
    """

    def __init__(
        self,
        headers_to_split_on: Optional[List[Tuple[str, str]]] = None,
        strip_headers: bool = False,
    ):
        self.headers_to_split_on = headers_to_split_on or [
            ("#", "h1"),
            ("##", "h2"),
            ("###", "h3"),
        ]
        self.strip_headers = strip_headers
        self._header_pattern = self._build_header_pattern()

    def _build_header_pattern(self) -> str:
        header_levels = [h[0] for h in self.headers_to_split_on]
        escaped = [re.escape(h) for h in header_levels]
        pattern = r"^(" + "|".join(escaped) + r")\s+(.+)$"
        return pattern

    def split_text(self, text: str) -> List[Document]:
        if not text or not text.strip():
            return []

        header_map = {h[0]: h[1] for h in self.headers_to_split_on}
        lines = text.split("\n")

        current_headers: Dict[str, str] = {}
        current_content: List[str] = []
        documents: List[Document] = []

        for line in lines:
            match = re.match(self._header_pattern, line)
            if match:
                header_prefix = match.group(1)
                header_text = match.group(2).strip()

                if header_prefix in header_map:
                    if current_content:
                        content = "\n".join(current_content).strip()
                        if content:
                            documents.append(Document(
                                page_content=content,
                                metadata=dict(current_headers),
                            ))
                        current_content = []

                    current_headers[header_map[header_prefix]] = header_text

                    lower_headers = {k: v for k, v in self.headers_to_split_on if len(k) > len(header_prefix)}
                    for lh_key, lh_name in lower_headers:
                        if lh_name in current_headers:
                            del current_headers[lh_name]

                    if not self.strip_headers:
                        current_content.append(line)
                else:
                    current_content.append(line)
            else:
                current_content.append(line)

        if current_content:
            content = "\n".join(current_content).strip()
            if content:
                documents.append(Document(
                    page_content=content,
                    metadata=dict(current_headers),
                ))

        return documents