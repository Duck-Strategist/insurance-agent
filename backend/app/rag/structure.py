"""把解析出的 Markdown 规整成「带层级的章节」。全部是规则，不调用 LLM。

为什么不用 LLM 改写（原教学项目的做法）：
- 长条款一次性改写会被 max_tokens 截断；
- 无法保证不改动条款原文，而保险条款一字之差就是赔与不赔。

这里只做「删行、拆行、接行、改标题标记」，正文一个字都不改。处理步骤：
1. 删噪声：图片、页码、跨页重复的页眉页脚、带点线的目录行、一行挤了多个目录项的行；
2. 删目录块：从"目录"标记到正文第一章真正开始之前（判断依据：第 1 章编号出现第二次）；
3. 拆双栏行：双栏排版的条款，MinerU 会把左栏标题和右栏正文合成一行
   （"1.1 合同构成 本合同由……"），拆成标题 + 正文；
4. 接断行：PDF 换行/分页截断的半句话接回去；
5. 识别标题：按条款编号（第X部分 / 第X条 / （一） / 一、 / （1） / 1. / 1.1 / 1.1.1），
   各类编号按「首次出现的顺序」分配层级，中文、数字两套编号体系都适用；
   不依赖 MinerU 给出的 # 级别（它经常不准，还会把整句话标成标题）。
   没有编号的 # 标题（附表里的疾病名等）挂在最近的编号标题下面。

这些规则都来自对 10 份真实条款解析结果的逐份检查，每条都有对应的回归测试。
"""

import re
from collections import Counter
from dataclasses import dataclass, field

CN_NUM = "一二三四五六七八九十百零〇两"

# (编号类型, 正则)。顺序决定同一行同时匹配多个时的优先级：更具体的在前
HEADING_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("part", re.compile(rf"^第[{CN_NUM}\d]+(部分|章)(?=[\s　]|$)")),
    ("article", re.compile(rf"^第[{CN_NUM}\d]+条(?=[\s　]|$)")),
    ("cn_paren", re.compile(rf"^[（(][{CN_NUM}]+[)）]")),
    ("cn_dun", re.compile(rf"^[{CN_NUM}]+、")),  # 一、二、
    ("num_paren", re.compile(r"^[（(]\d{1,2}[)）]")),  # （1）（2）
    ("dec3", re.compile(r"^\d{1,2}\.\d{1,2}\.\d{1,2}(?!\d)\.?(?=[\s　]|[^\d.])")),
    ("dec2", re.compile(r"^\d{1,2}\.\d{1,2}(?![\d.])(?=[\s　]|[^\d])")),
    ("dec1", re.compile(r"^\d{1,2}[\.．、](?![\d])")),
]
# "1 合同订立"（数字后无点）只在 MinerU 标了 # 时才认作标题，否则容易误伤正文里的数字开头
DEC1_NO_DOT = re.compile(r"^\d{1,2}\s+[^\d\s.．]")
# 数字编号的天然层级：1. < 1.1 < 1.1.1，无论首次出现顺序如何都保持这个相对关系
DECIMAL_ORDER = ["dec1", "dec2", "dec3"]

MAX_HEADING_CHARS = 40  # 超过这个长度的编号行视为正文里的列表项，而不是标题
MAX_MARKED_HEADING_CHARS = 60  # MinerU 标了 # 的行放宽一些
MAX_UNNUMBERED_HEADING_CHARS = 20  # 没编号的 # 行：超过就是被误标的半句话
MAX_LEVEL = 8
SENTENCE_END = ("。", "；", ";", "：", ":", "，", ",")
CLAUSE_PUNCT = re.compile(r"[，,。；;：:]")

# 没编号但属于顶层的标题（附表、释义等），其余没编号的标题挂在最近的编号标题下
TOP_LEVEL_UNNUMBERED = re.compile(r"^(附表|附录|附件|释义|阅读指引|条款目录|目\s*录)")

IMAGE_LINE = re.compile(r"^!\[[^\]]*\]\([^)]*\)$")
PAGE_NUMBER_LINE = re.compile(
    r"^(?:[-—–]\s*\d+\s*[-—–]|\d{1,3}|第\s*\d+\s*页(?:\s*[,，/]?\s*共\s*\d+\s*页)?|\d+\s*/\s*\d+)$"
)
TOC_LINE = re.compile(r"(\.{4,}|…{2,}|·{4,})\s*[\d.、]+\s*$")
TOC_MARKER = re.compile(r"^(条款)?目\s*录$")
# 一行里的编号（行首或空格后），用来识别"三栏目录挤成一行"
NUMBER_TOKEN = re.compile(r"(?:^|\s)\d{1,2}(?:[.．]\d{1,2}){0,2}[.．]?\s+\S")
# 双栏合并行：编号 + 短标题（无标点） + 空格 + 正文
INLINE_HEADING = re.compile(
    r"^(?P<head>(?:\d{1,2}\.\d{1,2}(?:\.\d{1,2})?|第[" + CN_NUM + r"\d]+条)\s*"
    r"[^\s，,。；;：:“”\"]{2,14})\s+(?P<body>\S.*)$"
)
FIRST_ORDINAL = re.compile(r"^(?:(?P<dec>1)(?:[.．、]|\s)|(?P<cn>第一(?:部分|章|条)))")
MD_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")

# 一行接近 PDF 整行宽度、结尾没有句末/闭合标点，说明是被换行或分页截断的半句话
BROKEN_LINE_MIN_CHARS = 30
LINE_END_PUNCT = (
    *("。", "；", ";", "：", ":", "！", "？", "!", "?"),
    *("）", ")", "」", "』", "”", '"'),
)


@dataclass
class Line:
    text: str
    marked_heading: bool  # MinerU 输出时带 # 标记
    is_table: bool = False


@dataclass
class Section:
    path: list[str]  # 从顶层到本节的标题，如 ["第二部分 保险责任", "第五条 保险责任", "（一）..."]
    body: list[str] = field(default_factory=list)  # 段落列表（表格整体为一个段落）

    @property
    def text(self) -> str:
        return "\n\n".join(self.body)


@dataclass
class StructureResult:
    title: str | None
    sections: list[Section]
    stats: dict[str, object]


def _split_lines(markdown: str) -> list[Line]:
    """按行拆分；HTML 表格（MinerU 用 <table> 输出表格）整体作为一行，不拆。"""
    lines: list[Line] = []
    table_buf: list[str] | None = None
    for raw in markdown.replace("\r\n", "\n").split("\n"):
        stripped = raw.strip().replace("　", " ")
        if table_buf is not None:
            table_buf.append(stripped)
            if "</table>" in stripped:
                lines.append(Line(" ".join(table_buf), marked_heading=False, is_table=True))
                table_buf = None
            continue
        if stripped.startswith("<table"):
            if "</table>" in stripped:
                lines.append(Line(stripped, marked_heading=False, is_table=True))
            else:
                table_buf = [stripped]
            continue
        if not stripped:
            continue
        m = MD_HEADING.match(stripped)
        if m:
            lines.append(Line(m.group(2).strip(), marked_heading=True))
        else:
            lines.append(Line(stripped, marked_heading=False))
    if table_buf:  # 未闭合的表格也保留
        lines.append(Line(" ".join(table_buf), marked_heading=False, is_table=True))
    return lines


def heading_kind(line: Line) -> str | None:
    """有编号的标题返回编号类型，否则 None。"""
    if line.is_table:
        return None
    text = line.text
    limit = MAX_MARKED_HEADING_CHARS if line.marked_heading else MAX_HEADING_CHARS
    if len(text) > limit:
        return None
    # 以句末标点结尾的是句子或列表项（如"1. 被保险人身故的；"），不是标题。
    # MinerU 有时会把整句标成 #（如"## 一、本保险合同保险金额由...约定。"），同样按正文处理
    if text.endswith(SENTENCE_END):
        return None
    # 标题里不会出现逗号句号等句内标点；有的是被截断的半句话（"（3）由……（CDR，Clinical"）
    if CLAUSE_PUNCT.search(text):
        return None
    for kind, pattern in HEADING_PATTERNS:
        if pattern.match(text):
            return kind
    if line.marked_heading and DEC1_NO_DOT.match(text):
        return "dec1"
    return None


def is_unnumbered_heading(line: Line) -> bool:
    """MinerU 标了 # 但没编号的行：短、且不含句内标点才算标题，否则是被误标的半句话。"""
    text = line.text
    if not line.marked_heading or heading_kind(line) is not None:
        return False
    if CLAUSE_PUNCT.search(text):
        return False
    if TOP_LEVEL_UNNUMBERED.match(text):
        # "附表四 甲状腺癌的TNM分期采用……"这种标题后面粘了正文的，按正文处理
        return len(text) <= MAX_MARKED_HEADING_CHARS // 2
    return len(text) <= MAX_UNNUMBERED_HEADING_CHARS


def _remove_noise(lines: list[Line], removed: Counter[str]) -> list[Line]:
    # 页眉页脚：同一短行出现 3 次以上且不是标题
    counts = Counter(ln.text for ln in lines if not ln.is_table and len(ln.text) <= 40)
    repeated = {t for t, c in counts.items() if c >= 3}

    kept: list[Line] = []
    for ln in lines:
        if ln.is_table:
            kept.append(ln)
            continue
        text = ln.text
        if IMAGE_LINE.match(text):
            removed["image"] += 1
        elif PAGE_NUMBER_LINE.match(text):
            removed["page_number"] += 1
        elif TOC_LINE.search(text):
            removed["toc"] += 1
        elif len(NUMBER_TOKEN.findall(text)) >= 2 and not CLAUSE_PUNCT.search(text):
            # 多栏目录挤成一行："1.1 合同构成 4.5 宣告死亡处理 11.3 合同内容变更"
            removed["toc"] += 1
        elif text in repeated and heading_kind(ln) is None:
            removed["repeated_header_footer"] += 1
        else:
            kept.append(ln)
    return kept


def _drop_toc_block(lines: list[Line], removed: Counter[str]) -> list[Line]:
    """删除从"目录"标记到正文第一章开始之前的整块内容。

    目录里的条目和正文标题一模一样，留着会生成一堆空章节，还会干扰断行合并。
    判断依据：目录标记之后，"第 1 章"的编号（1. / 1 / 第一部分 / 第一条）出现了两次，
    第一次在目录里，第二次才是正文。只出现一次说明没有目录块，不删。
    """
    marker = next((i for i, ln in enumerate(lines) if TOC_MARKER.match(ln.text)), None)
    if marker is None:
        return lines

    def ordinal(ln: Line) -> str | None:
        m = None if ln.is_table else FIRST_ORDINAL.match(ln.text)
        return (m.group("dec") or m.group("cn")) if m else None

    # 目录里的第一个"第 1 章"编号，正文开始于同一编号第二次出现处
    # （必须是同一种编号："第一部分"之后出现"第一条"是正文的下一级，不是目录结束）
    first = next((i for i in range(marker + 1, len(lines)) if ordinal(lines[i])), None)
    token = ordinal(lines[first]) if first is not None else None
    second = (
        next((i for i in range(first + 1, len(lines)) if ordinal(lines[i]) == token), None)
        if first is not None
        else None
    )
    if second is None:
        # 没有成块的目录（目录项已被逐行删掉，或根本没有）：只删"目录"这一行
        removed["toc"] += 1
        return lines[:marker] + lines[marker + 1 :]
    removed["toc_block"] += second - marker
    return lines[:marker] + lines[second:]


def _split_inline_headings(lines: list[Line], removed: Counter[str]) -> list[Line]:
    """双栏排版：把"1.1 合同构成 本合同由……"拆成标题行 + 正文行。"""
    out: list[Line] = []
    for ln in lines:
        m = None
        if not ln.is_table and len(ln.text) > MAX_HEADING_CHARS:
            m = INLINE_HEADING.match(ln.text)
        if m:
            out.append(Line(m.group("head"), marked_heading=False))
            out.append(Line(m.group("body"), marked_heading=False))
            removed["split_inline_headings"] += 1
        else:
            out.append(ln)
    return out


def _join_broken_lines(lines: list[Line], removed: Counter[str]) -> list[Line]:
    """把被 PDF 换行/分页拆开的半句话接回去（只合并换行，不改文字）。

    例：「（四）……医疗机构或司法鉴定机」+「构出具的残疾程度鉴定诊断书；」。
    不接的话，前半句带编号又没有句号，会被误判成标题。
    真正的标题很短（通常 < 30 字），不会被这条规则误伤。
    """
    out: list[Line] = []
    for ln in lines:
        prev = out[-1] if out else None
        if (
            prev is not None
            and not prev.is_table
            and not prev.marked_heading
            and not ln.is_table
            and not ln.marked_heading
            and len(prev.text) >= BROKEN_LINE_MIN_CHARS
            and not prev.text.endswith(LINE_END_PUNCT)
            and heading_kind(ln) is None
        ):
            prev.text += ln.text
            removed["joined_broken_lines"] += 1
            continue
        out.append(ln)
    return out


DECIMAL_PREFIX = re.compile(r"^(\d{1,2})(?:[.．](\d{1,2}))?(?:[.．](\d{1,2}))?")


def decimal_numbers(text: str) -> tuple[int, ...] | None:
    m = DECIMAL_PREFIX.match(text)
    return tuple(int(g) for g in m.groups() if g is not None) if m else None


def _assign_levels(kinds_in_order: list[str]) -> dict[str, int]:
    """编号类型 → 层级（1 起）。按首次出现顺序，数字编号之间保持 1. < 1.1 < 1.1.1。"""
    seen: list[str] = []
    for k in kinds_in_order:
        if k not in seen:
            seen.append(k)
    # 把数字编号按天然顺序重排到它们首次出现过的位置上
    dec_positions = [i for i, k in enumerate(seen) if k in DECIMAL_ORDER]
    dec_sorted = [k for k in DECIMAL_ORDER if k in seen]
    for pos, k in zip(dec_positions, dec_sorted, strict=True):
        seen[pos] = k
    return {k: min(i + 1, MAX_LEVEL) for i, k in enumerate(seen)}


def build_sections(markdown: str, *, title_hint: str | None = None) -> StructureResult:
    removed: Counter[str] = Counter()
    lines = _split_lines(markdown)
    lines = _remove_noise(lines, removed)
    lines = _drop_toc_block(lines, removed)
    lines = _split_inline_headings(lines, removed)
    # 删完噪声再接断行：页眉页脚夹在中间时，删掉后跨页的半句话才相邻
    lines = _join_broken_lines(lines, removed)

    # 文档标题：第一条带 # 且不带编号的行（通常是条款名或公司名），不进入章节路径
    title: str | None = None
    for i, ln in enumerate(lines):
        if heading_kind(ln) is not None:
            break
        if ln.marked_heading:
            title = ln.text
            lines = lines[:i] + lines[i + 1 :]
            break

    kinds = [heading_kind(ln) for ln in lines]
    levels = _assign_levels([k for k in kinds if k is not None])

    sections: list[Section] = []
    # 栈里记录 (层级, 标题)。入栈时弹出所有层级 >= 当前层级的标题：
    # 这样即使某类编号的层级号跳了一级（如 一、是 3 级、（一）是 4 级，
    # 但（一）直接出现在"第X条"下），同级的（一）（二）也会是兄弟而不是互相嵌套
    # 第三项是数字编号拆出的数字（如 "2.4.3" → (2, 4, 3)），非数字编号为 None
    stack: list[tuple[int, str, tuple[int, ...] | None]] = []
    # 最近一个编号标题（或附表类顶层标题）的层级；没编号的标题挂在它下面一级
    anchor_level = 0
    current = Section(path=[])
    heading_counts: Counter[str] = Counter()

    for ln, kind in zip(lines, kinds, strict=True):
        level: int | None = None
        if kind is not None:
            level = levels[kind]
            anchor_level = level
        elif is_unnumbered_heading(ln):
            if TOP_LEVEL_UNNUMBERED.match(ln.text):
                kind, level, anchor_level = "appendix", 1, 1
            else:
                # 如附表里的疾病名"恶性肿瘤——重度"：同一锚点下的多个互为兄弟
                kind, level = "unnumbered", anchor_level + 1
        if level is None:
            current.body.append(ln.text)
            continue
        heading_counts[kind or ""] += 1
        sections.append(current)
        while stack and stack[-1][0] >= level:
            stack.pop()
        nums = decimal_numbers(ln.text) if kind in DECIMAL_ORDER else None
        if nums is not None:
            # 数字编号的上级必须是它的前缀："9.4" 只能挂在 "9." 下。
            # 不一致说明栈里有被误判成标题的列表项（如正文里的"2. 重大自然灾害…"），去掉它
            before = len(stack)
            stack = [e for e in stack if e[2] is None or nums[: len(e[2])] == e[2]]
            removed["inconsistent_parent_dropped"] += before - len(stack)
        stack.append((level, ln.text, nums))
        current = Section(path=[text for _, text, _ in stack])
    sections.append(current)

    non_empty = [s for s in sections if s.body]
    body_chars = sum(len(s.text) for s in non_empty)
    stats: dict[str, object] = {
        "input_chars": len(markdown),
        "body_chars": body_chars,
        "removed_lines": dict(removed),
        "headings": dict(heading_counts),
        "levels": levels,
        "sections": len(non_empty),
        "title": title or title_hint,
    }
    return StructureResult(title=title or title_hint, sections=non_empty, stats=stats)
