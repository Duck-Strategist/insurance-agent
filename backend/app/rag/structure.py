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
    # 允许"第 1章""第 3 章"这类带空格、阿拉伯数字的写法
    ("part", re.compile(rf"^第\s*[{CN_NUM}\d]+\s*(部分|章)(?=[\s　]|$)")),
    ("article", re.compile(rf"^第\s*[{CN_NUM}\d]+\s*条(?=[\s　]|$)")),
    ("cn_paren", re.compile(rf"^[（(][{CN_NUM}]+[)）]")),
    ("cn_dun", re.compile(rf"^[{CN_NUM}]+、")),  # 一、二、
    ("num_paren", re.compile(r"^[（(]\d{1,2}[)）]")),  # （1）（2）
    # 末级编号允许 3 位：信美的重度疾病编到 8.1.117（曾因只认 2 位，8.1.100 以后的病种全部识别不出）
    ("dec4", re.compile(r"^\d{1,2}\.\d{1,2}\.\d{1,2}\.\d{1,3}(?!\d)\.?(?=[\s　]|[^\d.]|$)")),
    ("dec3", re.compile(r"^\d{1,2}\.\d{1,2}\.\d{1,3}(?!\d)\.?(?=[\s　]|[^\d.]|$)")),
    # 末尾的 |$：MinerU 有时只留下编号、标题文字丢了（达尔文 12 号的「## 2.3.3.4」），
    # 仍要当作该级标题，否则它下面的（1）（2）（3）会挂到上一个条目下
    ("dec2", re.compile(r"^\d{1,2}\.\d{1,3}(?![\d.])(?=[\s　]|[^\d]|$)")),
    ("dec1", re.compile(r"^\d{1,2}[\.．、](?![\d])")),
]
# "1 合同订立"（数字后无点）只在 MinerU 标了 # 时才认作标题，否则容易误伤正文里的数字开头
DEC1_NO_DOT = re.compile(r"^\d{1,2}\s+[^\d\s.．]")
# 数字编号的天然层级：1. < 1.1 < 1.1.1，无论首次出现顺序如何都保持这个相对关系
DECIMAL_ORDER = ["dec1", "dec2", "dec3", "dec4"]

MAX_HEADING_CHARS = 40  # 超过这个长度的编号行视为正文里的列表项，而不是标题
MAX_MARKED_HEADING_CHARS = 60  # MinerU 标了 # 的行放宽一些
MAX_UNNUMBERED_HEADING_CHARS = 20  # 没编号的 # 行：超过就是被误标的半句话
# 附表、释义里的病种名可能更长（「器官移植导致的人类免疫缺陷病毒（HIV）感染」22 字）。
# 只在这些部分放宽：正文里长的 # 行多是被误标的半句话，附表里则基本是病种名
MAX_APPENDIX_HEADING_CHARS = 30
# 条款名在附表前后会再出现一次（「复星联合医联有盟重大疾病保险（互联网）条款」），不是病种名
CLAUSE_TITLE = re.compile(r"(条款|（分红型）)$")
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
    r"^(?P<head>(?:\d{1,2}\.\d{1,2}(?:\.\d{1,3}){0,2}|第[" + CN_NUM + r"\d]+条)\s*"
    r"[^\s，,。；;：:“”\"]{2,14})\s+(?P<body>\S.*)$"
)
# 注意 (?!\d)：不能把 "1.1 合同构成" 当成第 1 章
FIRST_ORDINAL = re.compile(
    r"^(?:(?P<dec>1)(?:[.．、](?!\d)|\s)|(?P<cn>第\s*(?:一|1(?!\d))\s*(?:部分|章|条)))"
)
ARABIC_CHAPTER = re.compile(r"^第\s*(\d{1,2})\s*章")
MD_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
# 标题里的排版噪声：PDF 图标字体的私用区字符（如「##  条款目录」前的 U+F043 图标）、
# MinerU 给小字号文字加的 <sub> 标签。只清理 # 标题行，正文不动（D28）。
# 不清理的话「条款目录」认不出，整块目录留在正文里
HEADING_NOISE = re.compile("[\ue000-\uf8ff]|</?sub>")

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
            lines.append(Line(HEADING_NOISE.sub("", m.group(2)).strip(), marked_heading=True))
        else:
            lines.append(Line(stripped, marked_heading=False))
    if table_buf:  # 未闭合的表格也保留
        lines.append(Line(" ".join(table_buf), marked_heading=False, is_table=True))
    return lines


def _parens_balanced(text: str) -> bool:
    depth = 0
    for ch in text:
        if ch in "（(":
            depth += 1
        elif ch in "）)":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


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
    # 含句内标点的编号行多半是被截断的半句话（"（3）由具有评估资格的专科医生……（CDR，Clinical"，
    # 括号没有闭合）。但 MinerU 标了 # 且括号配对的，可能是真标题，如
    # "八十四、语言能力完全丧失，或严重咀嚼吞咽功能障碍"、
    # "八十八、美国纽约心脏病学会（…，NYHA）…分级"
    if CLAUSE_PUNCT.search(text) and not (line.marked_heading and _parens_balanced(text)):
        return None
    for kind, pattern in HEADING_PATTERNS:
        if pattern.match(text):
            return kind
    if line.marked_heading and DEC1_NO_DOT.match(text):
        return "dec1"
    return None


def is_unnumbered_heading(line: Line, *, in_appendix: bool = False) -> bool:
    """MinerU 标了 # 但没编号的行：短、且不含句内标点才算标题，否则是被误标的半句话。

    in_appendix：当前在附表/释义等顶层部分里，病种名可以长一些（MAX_APPENDIX_HEADING_CHARS），
    但条款名（以「条款」结尾）除外。
    """
    text = line.text
    if not line.marked_heading or heading_kind(line) is not None:
        return False
    if CLAUSE_PUNCT.search(text):
        return False
    if TOP_LEVEL_UNNUMBERED.match(text):
        # "附表四 甲状腺癌的TNM分期采用……"这种标题后面粘了正文的，按正文处理
        return len(text) <= MAX_MARKED_HEADING_CHARS // 2
    if in_appendix and not CLAUSE_TITLE.search(text) and _parens_balanced(text):
        return len(text) <= MAX_APPENDIX_HEADING_CHARS
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


def _is_prose(line: Line) -> bool:
    """完整的句子（目录项不会有句内标点，更不会有句号）。"""
    text = line.text
    if line.is_table or not CLAUSE_PUNCT.search(text):
        return False
    return "。" in text or len(text) > MAX_HEADING_CHARS


TABLE_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
TOC_ENTRY = re.compile(r"^\d{1,2}(?:\.\d{1,3}){1,3}\s*\S")


def _is_toc_table(line: Line) -> bool:
    """目录被 MinerU 识别成表格（信美：「9.29 复利」「9.30 有效身份证件」……）。

    判定：非空单元格里至少 70% 是「编号 + 短标题」，且没有句号。正文里的表格
    （费率表、疾病定义表）单元格是数字或句子，不会满足。
    """
    if not line.is_table or "。" in line.text:
        return False
    cells = [c.strip() for c in TABLE_CELL.findall(line.text) if c.strip()]
    if len(cells) < 4:
        return False
    return sum(1 for c in cells if TOC_ENTRY.match(c)) / len(cells) >= 0.7


def _heading_title(text: str) -> str:
    """去掉开头编号和所有空白、标点后的标题文字，用于比较目录项和正文标题。"""
    stripped = FIRST_ORDINAL.sub("", text, count=1)
    return re.sub(r"[\s\W_]+", "", stripped)


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

    # 目录里的第一个"第 1 章"，正文开始于同一标题的"第 1 章"再次出现处。
    # - 以标题文字为准：目录项常已被逐行删掉，此时"第一次"就是正文标题，后面恰好也以
    #   "1." 开头的行只是巧合（曾因只比编号而误删了 3 份条款的第 1 章标题和正文）；
    #   "第一部分 总则"之后的"第一条 合同构成"标题不同，也不会被误认为目录结束。
    # - 不要求编号写法相同：中英条款目录写"1 您与我们的保险合同"，正文写"第 1章 您与……"
    first = next((i for i in range(marker + 1, len(lines)) if ordinal(lines[i])), None)
    second = None
    title = ""
    if first is not None:
        title = _heading_title(lines[first].text)
        second = next(
            (
                i
                for i in range(first + 1, len(lines))
                if ordinal(lines[i]) and _heading_title(lines[i].text) == title
            ),
            None,
        )
    if second is None or not title:
        # 没有成块的目录（目录项大多已被逐行删掉）：删"目录"这一行，以及紧跟其后、
        # 连续的未标 # 的短编号行（多栏目录的残余，如福有余的"12.17 军事冲突"…"12.22 情形复杂"），
        # 遇到 # 标题、完整句子或"第 1 章"编号（正文从这里开始）即停。
        # 不删的话它们会成为后面正文的"上级标题"
        end = marker + 1
        while end < len(lines) and (
            _is_toc_table(lines[end])
            or (
                not lines[end].marked_heading
                and not _is_prose(lines[end])
                and heading_kind(lines[end]) is not None
                and not ordinal(lines[end])
            )
        ):
            end += 1
        removed["toc"] += end - marker
        return lines[:marker] + lines[end:]
    # 目录块内只删像目录项的行；完整的句子（如"本公司在每页底部对专业名词做了释义……"）保留
    kept = [ln for ln in lines[marker + 1 : second] if _is_prose(ln)]
    removed["toc_block"] += (second - marker) - len(kept)
    return lines[:marker] + kept + lines[second:]


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


DECIMAL_PREFIX = re.compile(r"^(\d{1,2})(?:[.．](\d{1,3}))?(?:[.．](\d{1,3}))?(?:[.．](\d{1,3}))?")


def decimal_numbers(text: str) -> tuple[int, ...] | None:
    chapter = ARABIC_CHAPTER.match(text)
    if chapter:  # "第 2 章" 视同 "2."，这样 "2.1" 不会挂到 "第 1章" 下面
        return (int(chapter.group(1)),)
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


def _line_levels(kinds: list[str | None], levels: dict[str, int]) -> list[int | None]:
    """每行标题的层级。

    默认按全文「首次出现顺序」分配（levels）。但当最高一级是「第X部分/章」时，每个部分内部
    重新按首次出现顺序分配：众安条款前面的保险责任里先出现「（一）」、释义里才出现「一、」，
    按全文顺序「一、」比「（一）」深，释义里「八十四、语言能力完全丧失……」就挂到了
    「（十三）原发性肉碱缺乏症」下面（spec Q12）。各部分的编号体系本来就各自独立。
    """
    if not levels or min(levels, key=lambda k: levels[k]) != "part":
        return [levels[k] if k is not None else None for k in kinds]
    out: list[int | None] = []
    segment_start = 0

    def assign(segment: list[str | None]) -> None:
        inner = _assign_levels([k for k in segment if k is not None and k != "part"])
        for k in segment:
            out.append(None if k is None else 1 if k == "part" else inner[k] + 1)

    for i, k in enumerate(kinds):
        if k == "part" and i > segment_start:
            assign(kinds[segment_start:i])
            segment_start = i
    assign(kinds[segment_start:])
    return out


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
    # 章节用"第 N 章 + N.M"编号时，章已经占了第一级，正文里的"1. 身故保险金""2. ……"只是列表项。
    # 否则"2.2 责任免除"会被挂到列表项"2. 重大自然灾害意外身故保险金"下面
    if "dec2" in kinds and any(
        k == "part" and ARABIC_CHAPTER.match(ln.text) for ln, k in zip(lines, kinds, strict=True)
    ):
        removed["dec1_as_list_item"] += kinds.count("dec1")
        kinds = [None if k == "dec1" else k for k in kinds]
    levels = _assign_levels([k for k in kinds if k is not None])
    line_levels = _line_levels(kinds, levels)

    sections: list[Section] = []
    # 栈里记录 (层级, 标题)。入栈时弹出所有层级 >= 当前层级的标题：
    # 这样即使某类编号的层级号跳了一级（如 一、是 3 级、（一）是 4 级，
    # 但（一）直接出现在"第X条"下），同级的（一）（二）也会是兄弟而不是互相嵌套
    # 第三项是数字编号拆出的数字（如 "2.4.3" → (2, 4, 3)），非数字编号为 None
    stack: list[tuple[int, str, tuple[int, ...] | None]] = []
    # 最近一个编号标题（或附表类顶层标题）的层级；没编号的标题挂在它下面一级
    anchor_level = 0
    # 当前是否在附表、释义这类没编号的顶层部分里（其中的病种名可以长一些）
    in_appendix = False
    current = Section(path=[])
    heading_counts: Counter[str] = Counter()

    for ln, kind, line_level in zip(lines, kinds, line_levels, strict=True):
        level: int | None = None
        if kind is not None:
            level = line_level
            anchor_level = level or 0
            if level == 1:
                in_appendix = False
        elif is_unnumbered_heading(ln, in_appendix=in_appendix):
            if TOP_LEVEL_UNNUMBERED.match(ln.text):
                kind, level, anchor_level = "appendix", 1, 1
                in_appendix = not ln.text.startswith(("阅读指引", "条款目录", "目"))
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
        nums = decimal_numbers(ln.text) if kind in (*DECIMAL_ORDER, "part") else None
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
