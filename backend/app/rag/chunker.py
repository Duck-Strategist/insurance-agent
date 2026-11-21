"""父子块切分。

- 父块 = 一个章节的完整正文（太长时按段落拆成多个，路径相同），用于回答上下文与引用展示；
- 子块 = 父块内约 child_chars 字的片段（按句子边界切，带重叠），用于检索；
- 子块向量化时在前面拼上「条款名｜章节路径」，补充"这段话属于哪个条款哪一节"的语义；
- 长表格（如附表里的疾病定义表，可达数千字）按行拆分，每段都带上表头，仍是完整可读的表。
"""

import re
from dataclasses import dataclass, field

from app.rag.structure import Section

# 句末标点或换行（段落边界）之后都可以切
SENTENCE_SPLIT = re.compile(r"(?<=[。！？；!?;\n])")
TABLE_ROW = re.compile(r"<tr\b.*?</tr>", re.S | re.I)

# 子块里的表格允许比普通文本长一些：一行疾病定义本身就可能有几百字
TABLE_CHILD_FACTOR = 2


def split_table(html: str, max_chars: int) -> list[str]:
    """按行把长表格拆成多段，每段都以原表头（第一行）开头。

    不拆的话一张几千字的表只有一个向量，"单耳失聪怎么定义"这类问题很难命中其中一行。
    单行本身超长时保留整行（不在单元格中间截断）。
    """
    if len(html) <= max_chars:
        return [html]
    rows = TABLE_ROW.findall(html)
    if len(rows) < 2:
        return [html]
    header, body_rows = rows[0], rows[1:]
    pieces: list[str] = []
    current: list[str] = []
    size = len(header)
    for row in body_rows:
        if current and size + len(row) > max_chars:
            pieces.append(f"<table>{header}{''.join(current)}</table>")
            current, size = [], len(header)
        current.append(row)
        size += len(row)
    if current:
        pieces.append(f"<table>{header}{''.join(current)}</table>")
    return pieces


def _is_table(paragraph: str) -> bool:
    return paragraph.startswith("<table")


@dataclass
class ChildDraft:
    content: str
    embed_text: str  # 带上下文前缀，用于向量化和全文检索


@dataclass
class ParentDraft:
    section_path: list[str]
    content: str
    children: list[ChildDraft] = field(default_factory=list)


def _split_paragraphs(paragraphs: list[str], max_chars: int) -> list[list[str]]:
    """把段落列表按 max_chars 分组；单个超长段落单独成组（不在段内截断）。"""
    groups: list[list[str]] = []
    current: list[str] = []
    size = 0
    for p in paragraphs:
        if current and size + len(p) > max_chars:
            groups.append(current)
            current, size = [], 0
        current.append(p)
        size += len(p)
    if current:
        groups.append(current)
    return groups


def split_text(text: str, size: int, overlap: int) -> list[str]:
    """按句子边界切成约 size 字的片段，相邻片段重叠约 overlap 字。"""
    if len(text) <= size:
        return [text]
    sentences = [s for s in SENTENCE_SPLIT.split(text) if s]
    # 没有句号的超长句子（如长列表）按长度硬切
    units: list[str] = []
    for s in sentences:
        while len(s) > size:
            units.append(s[:size])
            s = s[size - overlap :]
        units.append(s)

    chunks: list[str] = []
    current = ""
    for u in units:
        if current and len(current) + len(u) > size:
            chunks.append(current)
            # 从上一块末尾取不超过 overlap 的完整句子作为重叠
            tail = ""
            for s in reversed(SENTENCE_SPLIT.split(current)):
                if not s:
                    continue
                if len(tail) + len(s) > overlap:
                    break
                tail = s + tail
            current = tail
        current += u
    if current:
        chunks.append(current)
    return chunks


def build_chunks(
    sections: list[Section],
    *,
    doc_title: str,
    parent_max_chars: int,
    child_chars: int,
    child_overlap: int,
) -> list[ParentDraft]:
    parents: list[ParentDraft] = []
    for section in sections:
        heading = section.path[-1] if section.path else ""
        prefix = f"{doc_title}｜{' > '.join(section.path)}" if section.path else doc_title
        # 先把超长表格拆开，父块才能按大小正常分组
        paragraphs = [
            piece
            for para in section.body
            for piece in (split_table(para, parent_max_chars) if _is_table(para) else [para])
        ]
        for group in _split_paragraphs(paragraphs, parent_max_chars):
            parent = ParentDraft(
                section_path=section.path,
                content="\n\n".join([heading, *group]) if heading else "\n\n".join(group),
            )
            # 相邻的普通段落合并后再切，避免产生大量几十字的碎片子块；
            # 表格按行拆（带表头），不按字数硬切：切在单元格中间的表格没法读
            pieces: list[str] = []
            buffer: list[str] = []
            for para in group:
                if _is_table(para):
                    if buffer:
                        pieces += split_text("\n".join(buffer), child_chars, child_overlap)
                        buffer = []
                    pieces += split_table(para, child_chars * TABLE_CHILD_FACTOR)
                else:
                    buffer.append(para)
            if buffer:
                pieces += split_text("\n".join(buffer), child_chars, child_overlap)

            parent.children = [ChildDraft(content=p, embed_text=f"{prefix}\n{p}") for p in pieces]
            parents.append(parent)
    return parents
