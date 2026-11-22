from app.rag.chunker import build_chunks, split_text
from app.rag.structure import build_sections

# 中文编号体系（第X部分 / 第X条 / （一） / 1.），含页眉页脚、页码、图片、目录噪声
CN_STYLE = """
# 太平洋小蜜蜂综合意外险条款

目录
第一条 保险合同构成......3

第一部分 总则

第一条 保险合同构成

本保险合同由保险条款、投保单组成。

太平洋财产保险股份有限公司

- 1 -

第二部分 保险责任

第五条 保险责任

在保险期间内，被保险人遭受意外伤害事故的，保险人按下列约定给付保险金：

（一）身故保险责任

被保险人自意外伤害事故发生之日起180日内身故的，保险人给付身故保险金。

1. 被保险人身故的；

（二）伤残保险责任

![](images/abc.jpg)

被保险人因意外伤害导致伤残的，按比例给付。

太平洋财产保险股份有限公司

- 2 -

太平洋财产保险股份有限公司
"""

# 数字编号体系（1. / 1.1 / 1.1.1），MinerU 把所有标题都标成了 #
DEC_STYLE = """
# 达尔文12号重大疾病保险条款

# 1. 您与我们的合同

# 1.1 合同构成

本合同是您与我们约定保险权利义务关系的协议。

# 2. 我们提供的保障

# 2.1 基本保险金额

基本保险金额由您在投保时与我们约定。

## 2.1.1 重大疾病保险金

被保险人确诊重大疾病的，我们按基本保险金额给付。

<table><tr><td>疾病</td><td>比例</td></tr>
<tr><td>恶性肿瘤</td><td>100%</td></tr></table>
"""


def paths(markdown: str) -> list[list[str]]:
    return [s.path for s in build_sections(markdown).sections]


def test_chinese_numbering_hierarchy() -> None:
    result = build_sections(CN_STYLE)
    assert result.title == "太平洋小蜜蜂综合意外险条款"
    assert paths(CN_STYLE) == [
        ["第一部分 总则", "第一条 保险合同构成"],
        ["第二部分 保险责任", "第五条 保险责任"],
        ["第二部分 保险责任", "第五条 保险责任", "（一）身故保险责任"],
        ["第二部分 保险责任", "第五条 保险责任", "（二）伤残保险责任"],
    ]
    assert result.stats["levels"] == {"part": 1, "article": 2, "cn_paren": 3}


def test_list_items_ending_with_punctuation_stay_in_body() -> None:
    body = build_sections(CN_STYLE).sections[2].text
    assert "1. 被保险人身故的；" in body


def test_noise_lines_are_removed_without_touching_body() -> None:
    result = build_sections(CN_STYLE)
    removed = result.stats["removed_lines"]
    assert removed == {
        "toc": 2,  # "目录" 标题 + 带页码的目录行
        "page_number": 2,
        "image": 1,
        "repeated_header_footer": 3,
    }
    all_text = "\n".join(s.text for s in result.sections)
    assert "太平洋财产保险股份有限公司" not in all_text
    assert "images/abc.jpg" not in all_text
    # 正文原样保留
    assert "被保险人自意外伤害事故发生之日起180日内身故的，保险人给付身故保险金。" in all_text


def test_decimal_numbering_ignores_minerU_heading_levels() -> None:
    assert paths(DEC_STYLE) == [
        ["1. 您与我们的合同", "1.1 合同构成"],
        ["2. 我们提供的保障", "2.1 基本保险金额"],
        ["2. 我们提供的保障", "2.1 基本保险金额", "2.1.1 重大疾病保险金"],
    ]


def test_decimal_levels_keep_natural_order_even_if_deeper_seen_first() -> None:
    md = "1.1 合同构成\n\n正文A\n\n1. 第一章\n\n1.1 小节\n\n正文B"
    result = build_sections(md)
    assert result.stats["levels"] == {"dec1": 1, "dec2": 2}
    assert [s.path for s in result.sections] == [["1.1 合同构成"], ["1. 第一章", "1.1 小节"]]


def test_table_is_kept_whole() -> None:
    section = build_sections(DEC_STYLE).sections[-1]
    tables = [p for p in section.body if p.startswith("<table")]
    assert len(tables) == 1 and "恶性肿瘤" in tables[0]


def test_long_numbered_line_is_body_not_heading() -> None:
    long_line = "1. " + "被保险人在保险期间内发生保险事故" * 4
    result = build_sections(f"第一条 责任\n\n{long_line}")
    assert [s.path for s in result.sections] == [["第一条 责任"]]
    assert long_line in result.sections[0].text


def test_split_text_respects_size_and_overlap() -> None:
    text = "".join(f"第{i}句话的内容比较长一些。" for i in range(40))
    pieces = split_text(text, size=100, overlap=30)
    assert all(len(p) <= 100 for p in pieces)
    # 相邻片段有重叠：后一片的开头（完整句子）来自前一片
    for a, b in zip(pieces, pieces[1:], strict=False):
        first_sentence = b.split("。")[0] + "。"
        assert first_sentence in a
    # 拼起来不丢内容
    assert all(f"第{i}句话" in "".join(pieces) for i in range(40))


def test_split_text_hard_cuts_sentence_without_punctuation() -> None:
    pieces = split_text("无标点" * 100, size=50, overlap=10)
    assert all(len(p) <= 50 for p in pieces)


def test_build_chunks_adds_context_prefix_and_keeps_tables() -> None:
    result = build_sections(DEC_STYLE)
    parents = build_chunks(
        result.sections,
        doc_title="达尔文12号",
        parent_max_chars=2000,
        child_chars=400,
        child_overlap=60,
    )
    assert len(parents) == 3
    last = parents[-1]
    assert last.content.startswith("2.1.1 重大疾病保险金")
    assert [c.content.startswith("<table") for c in last.children] == [False, True]
    assert last.children[0].embed_text.startswith(
        "达尔文12号｜2. 我们提供的保障 > 2.1 基本保险金额 > 2.1.1 重大疾病保险金\n"
    )


def test_long_section_splits_into_multiple_parents_with_same_path() -> None:
    md = "第一条 释义\n\n" + "\n\n".join("段落内容。" * 40 for _ in range(5))
    parents = build_chunks(
        build_sections(md).sections,
        doc_title="t",
        parent_max_chars=500,
        child_chars=200,
        child_overlap=20,
    )
    assert len(parents) == 3
    assert all(p.section_path == ["第一条 释义"] for p in parents)
    assert all(p.content.startswith("第一条 释义") for p in parents)


# ---- 以下用例来自 MinerU 真实输出中发现的问题 ----


def test_cn_dun_numbering_nests_under_article() -> None:
    md = (
        "## 第二条 被保险人和投保人\n\n## 一、被保险人\n\n身体健康的自然人。\n\n"
        "## 二、投保人\n\n投保人应具有完全民事行为能力。"
    )
    assert paths(md) == [
        ["第二条 被保险人和投保人", "一、被保险人"],
        ["第二条 被保险人和投保人", "二、投保人"],
    ]


def test_siblings_stay_siblings_when_level_numbers_skip() -> None:
    """一、 先出现（3 级），（一） 后出现（4 级）但直接挂在"第X条"下：（一）（二）应是兄弟。"""
    md = (
        "第一部分 总则\n\n第二条 投保人\n\n一、被保险人\n\n正文。\n\n"
        "第九条 保险责任\n\n（一）身故保险责任\n\n身故给付。\n\n（二）残疾保险责任\n\n残疾给付。"
    )
    assert paths(md) == [
        ["第一部分 总则", "第二条 投保人", "一、被保险人"],
        ["第一部分 总则", "第九条 保险责任", "（一）身故保险责任"],
        ["第一部分 总则", "第九条 保险责任", "（二）残疾保险责任"],
    ]


def test_marked_sentence_is_body_not_heading() -> None:
    md = (
        "## 第十二条 保险金额\n\n"
        "## 一、本保险合同保险金额由投保人与保险人在投保时约定。\n\n二、其他。"
    )
    result = build_sections(md)
    assert paths(md) == [["第十二条 保险金额"]]
    assert "一、本保险合同保险金额由投保人与保险人在投保时约定。" in result.sections[0].text


def test_line_broken_by_pdf_is_joined_and_not_mistaken_for_heading() -> None:
    md = (
        "## 二、残疾保险金申请\n\n（三）保险金申请人的身份证明；\n\n"
        "（四）中华人民共和国境内二级以上（含二级）或保险人认可的医疗机构或司法鉴定机\n\n"
        "构出具的残疾程度鉴定诊断书；"
    )
    result = build_sections(md)
    assert paths(md) == [["二、残疾保险金申请"]]
    assert "司法鉴定机构出具的残疾程度鉴定诊断书；" in result.sections[0].text
    assert result.stats["removed_lines"] == {"joined_broken_lines": 1}


def test_short_unpunctuated_heading_is_not_joined_with_next_line() -> None:
    md = "第一条 保险合同构成\n\n本保险合同由保险条款组成。"
    assert paths(md) == [["第一条 保险合同构成"]]


def test_decimal_parent_must_be_prefix() -> None:
    """正文列表项"2. 重大自然灾害…"被误认成标题后，"9.4"不能挂在它下面。"""
    md = (
        "# 1. 合同订立\n\n# 1.1 合同构成\n\n正文A。\n\n"
        "2. 重大自然灾害意外身故保险金\n\n说明。\n\n"
        "9.4 保险金给付\n\n正文B。"
    )
    result = build_sections(md)
    assert [s.path for s in result.sections][-1] == ["9.4 保险金给付"]
    assert result.stats["removed_lines"]["inconsistent_parent_dropped"] == 1


def test_split_table_keeps_header_in_every_piece() -> None:
    from app.rag.chunker import split_table

    header = "<tr><td>序号</td><td>疾病名称</td><td>定义</td></tr>"
    rows = [f"<tr><td>{i}</td><td>疾病{i}</td><td>{'定义内容' * 20}</td></tr>" for i in range(20)]
    html = f"<table>{header}{''.join(rows)}</table>"
    pieces = split_table(html, 600)
    assert len(pieces) > 1
    assert all(p.startswith(f"<table>{header}") and p.endswith("</table>") for p in pieces)
    assert all(len(p) <= 600 for p in pieces)
    # 所有数据行都在，且没有重复
    assert sum(p.count("<tr>") - 1 for p in pieces) == 20


def test_long_table_is_split_into_multiple_children() -> None:
    header = "<tr><td>序号</td><td>名称</td></tr>"
    rows = "".join(f"<tr><td>{i}</td><td>{'很长的定义' * 30}</td></tr>" for i in range(10))
    md = f"# 某重疾险条款\n\n# 附表一 重大疾病\n\n<table>{header}{rows}</table>"
    parents = build_chunks(
        build_sections(md).sections,
        doc_title="t",
        parent_max_chars=2500,
        child_chars=400,
        child_overlap=60,
    )
    children = [c for p in parents for c in p.children]
    # 10 行 × 约 180 字，子块上限 400×2 → 拆成 3 段，每段都带表头
    assert len(children) == 3
    assert all(c.content.startswith(f"<table>{header}") for c in children)
    assert parents[0].section_path == ["附表一 重大疾病"]
    assert all(len(p.content) <= 2500 + 20 for p in parents)


# ---- M2 代码审查发现的问题 ----


def test_toc_block_requires_same_title_not_just_same_number() -> None:
    """目录项已被逐行删掉时，"1. 合同订立"后面的"1.1 合同构成"不是目录结束，不能删中间的正文。"""
    md = (
        "# 某条款\n\n条款目录\n\n"
        "# 1. 合同订立\n\n“本条款”简称“本合同”。在本保险条款中，“您”指投保人。\n\n"
        "1.1 合同构成\n\n本合同由保险单组成。"
    )
    result = build_sections(md)
    assert [s.path for s in result.sections] == [
        ["1. 合同订立"],
        ["1. 合同订立", "1.1 合同构成"],
    ]
    assert "“您”指投保人" in result.sections[0].text
    assert "toc_block" not in result.stats["removed_lines"]


def test_toc_block_with_matching_title_is_dropped() -> None:
    md = (
        "# 某条款\n\n目录\n\n1. 合同订立\n\n2. 保障内容\n\n"
        "# 1. 合同订立\n\n# 1.1 合同构成\n\n正文。"
    )
    result = build_sections(md)
    assert [s.path for s in result.sections] == [["1. 合同订立", "1.1 合同构成"]]
    assert result.stats["removed_lines"]["toc_block"] == 3


def test_marked_heading_with_comma_and_balanced_parens_is_heading() -> None:
    md = (
        "# 第七部分 释义\n\n"
        "## 八十四、语言能力完全丧失，或严重咀嚼吞咽功能障碍\n\n指……。\n\n"
        "## 八十八、美国纽约心脏病学会（New York Heart Association，NYHA）心功能状态分级\n\n"
        "指……。"
    )
    assert [p[-1] for p in paths(md)] == [
        "八十四、语言能力完全丧失，或严重咀嚼吞咽功能障碍",
        "八十八、美国纽约心脏病学会（New York Heart Association，NYHA）心功能状态分级",
    ]


def test_truncated_line_with_unclosed_paren_is_body() -> None:
    md = (
        "# 1.2 阿尔茨海默病\n\n"
        "## （3） 由具有评估资格的专科医生根据临床痴呆评定量表（CDR，Clinical\n\n"
        "Dementia Rating）评估结果为 3 分。"
    )
    assert paths(md) == [["1.2 阿尔茨海默病"]]


def test_arabic_chapter_with_spaces_and_list_items() -> None:
    """中英条款：章用"第 1章"，节用"1.7"，正文里的"1. 身故保险金"是列表项。第 2 章标题缺失。"""
    md = (
        "# 中英人寿条款\n\n"
        "## 第 1章 您与我们的保险合同\n\n## 1.7 未成年人身故保险金限制\n\n正文A。\n\n"
        "## 2.1 保险责任\n\n## 1. 身故保险金\n\n身故给付。\n\n"
        "## 2. 重大自然灾害意外身故保险金\n\n灾害给付。\n\n## 2.2 责任免除\n\n免责正文。\n\n"
        "## 第 3 章 红利\n\n## 3.1 红利的分配\n\n分红正文。"
    )
    result = build_sections(md)
    assert [s.path for s in result.sections] == [
        ["第 1章 您与我们的保险合同", "1.7 未成年人身故保险金限制"],
        ["2.1 保险责任"],  # 第 2 章标题缺失：不能挂到第 1 章下
        ["2.2 责任免除"],  # 不能挂到列表项"2. 重大自然灾害…"下
        ["第 3 章 红利", "3.1 红利的分配"],
    ]
    assert "1. 身故保险金" in result.sections[1].text
    assert result.stats["removed_lines"]["dec1_as_list_item"] == 2


def test_prose_inside_toc_block_is_kept() -> None:
    md = (
        "# 某条款\n\n条款目录\n\n1. 合同订立\n\n1.1 合同构成\n\n"
        "（本公司在每页底部对一些专业名词做了释义，这些释义为本条款的重要组成部分。）\n\n"
        "# 1. 合同订立\n\n# 1.1 合同构成\n\n正文。"
    )
    result = build_sections(md)
    all_text = "\n".join(s.text for s in result.sections)
    assert "本公司在每页底部对一些专业名词做了释义" in all_text
    assert result.stats["removed_lines"]["toc_block"] == 3


def test_toc_entry_and_body_heading_may_use_different_numbering_styles() -> None:
    md = (
        "# 中英人寿条款\n\n## 条款目录\n\n1 您与我们的保险合同\n\n1.1 合同构成\n\n"
        "## 第 1章 您与我们的保险合同\n\n## 1.1 合同构成\n\n正文。"
    )
    result = build_sections(md)
    assert [s.path for s in result.sections] == [["第 1章 您与我们的保险合同", "1.1 合同构成"]]


def test_leftover_toc_entries_after_marker_are_dropped() -> None:
    """福有余：多栏目录行已逐行删掉，但单独成行的尾部目录项残留，不能成为后文正文的上级。"""
    md = (
        "# 阅读指引\n\n您享有的权益。\n\n## 条款目录\n\n"
        "12.21 鉴定机构\n\n12.22 情形复杂\n\n"
        "# 中国太平洋人寿保险股份有限公司\n\n"
        "“本条款”简称“本合同”。在本保险条款中，“您”指投保人。\n\n"
        "## 1． 您与我们订立的合同\n\n1.1 合同构成\n\n本合同由保险单组成。"
    )
    result = build_sections(md)
    paths_ = [s.path for s in result.sections]
    assert not any("12.22 情形复杂" in p for p in paths_)
    definition = next(s for s in result.sections if "“您”指投保人" in s.text)
    assert definition.path == ["中国太平洋人寿保险股份有限公司"]


# ---- 2026-11 结构修复（spec Q12、Q13）：用例取自真实解析结果 ----


def joined_paths(md: str) -> list[str]:
    return [" > ".join(s.path) for s in build_sections(md).sections]


def body_of(md: str, path: str) -> str:
    return next(s.text for s in build_sections(md).sections if " > ".join(s.path) == path)


# 复星联合医联有盟：附表里 22 字的病种名（原规则只认 ≤20 字，整段正文挂到了上一个病种下）
APPENDIX_LONG_NAME = """
# 复星联合医联有盟重大疾病保险（互联网）条款

## 附表一 重大疾病

## 侵蚀性葡萄胎（或称恶性葡萄胎）

指异常增生的绒毛组织浸润性生长侵入子宫肌层或转移至其他器官或组织的葡萄胎，并已经进行化疗或手术治疗。

## 器官移植导致的人类免疫缺陷病毒（HIV）感染

指因进行器官移植而感染上人类免疫缺陷病毒（HIV），须满足下列全部条件：

## 复星联合医联有盟重大疾病保险（互联网）条款

（3） 提供器官移植治疗的器官移植中心或医院必须拥有合法经营执照。
"""


def test_long_disease_name_in_appendix_is_heading() -> None:
    assert joined_paths(APPENDIX_LONG_NAME) == [
        "附表一 重大疾病 > 侵蚀性葡萄胎（或称恶性葡萄胎）",
        "附表一 重大疾病 > 器官移植导致的人类免疫缺陷病毒（HIV）感染",
    ]
    hiv = "附表一 重大疾病 > 器官移植导致的人类免疫缺陷病毒（HIV）感染"
    # 附表里再次出现的条款名不是病种名，按正文留在上一节
    assert "复星联合医联有盟重大疾病保险（互联网）条款" in body_of(APPENDIX_LONG_NAME, hiv)
    葡萄胎 = body_of(APPENDIX_LONG_NAME, "附表一 重大疾病 > 侵蚀性葡萄胎（或称恶性葡萄胎）")
    assert "器官移植" not in 葡萄胎


def test_long_unnumbered_line_outside_appendix_is_still_body() -> None:
    # 只在附表/释义里放宽：正文里 22 字的 # 行仍按被误标的半句话处理
    md = """
## 第一条 保险责任

## 器官移植导致的人类免疫缺陷病毒（HIV）感染

正文。
"""
    assert joined_paths(md) == ["第一条 保险责任"]


# 信美星辰守护：8.1.100 以后的病种（编号末级 3 位）与 1.5.1.12 这类 4 级编号
DEEP_NUMBERS = """
## 1.5 保险责任

## 1.5.1 基本责任

## 1.5.1.12 卵圆孔未闭关爱保险金

卵圆孔未闭关爱保险金的给付以一次为限。

## 1.5.1.13 严重肥胖手术关爱保险金

被保险人于等待期后经医院的专科医生初次确诊。

## 8.1 重度疾病

## 8.1.93 肝豆状核变性

指由于铜代谢障碍所引起的一种疾病。

## 8.1.102 严重席汉氏综合征

指因产后大出血并发休克。

## 8.1.103 败血症导致的多器官功能障碍综合征

指败血症导致的多器官功能障碍。
"""


def test_four_level_and_three_digit_decimal_headings() -> None:
    assert joined_paths(DEEP_NUMBERS) == [
        "1.5 保险责任 > 1.5.1 基本责任 > 1.5.1.12 卵圆孔未闭关爱保险金",
        "1.5 保险责任 > 1.5.1 基本责任 > 1.5.1.13 严重肥胖手术关爱保险金",
        "8.1 重度疾病 > 8.1.93 肝豆状核变性",
        # 曾经挂在 8.1.93 下面：8.1.102 只认出了 "8.1.10" 前缀
        "8.1 重度疾病 > 8.1.102 严重席汉氏综合征",
        "8.1 重度疾病 > 8.1.103 败血症导致的多器官功能障碍综合征",
    ]


def test_inline_heading_with_three_digit_number_is_split() -> None:
    md = """
## 8.1 重度疾病

8.1.106 脑型疟疾 指恶性疟原虫严重感染导致的脑病或脑型疟疾，以昏迷为主要特征。
"""
    assert joined_paths(md) == ["8.1 重度疾病 > 8.1.106 脑型疟疾"]


TOC_ROWS = "<tr><td>9.31 组织病理学检查</td><td>9.37 肌力</td></tr></table>"
DISEASE_ROWS = (
    "<tr><td>1.1 严重脑损伤</td><td>指因头部遭受机械性外力。须满足下列条件。</td></tr>"
    "<tr><td>1.2 瘫痪</td><td>指因疾病或意外导致肢体机能丧失。</td></tr></table>"
)

# 信美、复星：标题前的图标字体字符（U+F043）与 <sub> 标签，导致「条款目录」认不出
ICON_HEADINGS = """
# 复星联合医联有盟重大疾病保险（互联网）条款

## \uf043<sub>拥有的重要权益</sub>

✿ 在犹豫期内您若要求解除合同，我们向您无息退还保险费

## \uf043 条款目录

{toc_table}

# 信美人寿相互保险社

## 1.1 基本保险金额

本合同的基本保险金额由您在投保时与我们约定。
""".format(
    toc_table="<table><tr><td>9.29 复利</td><td>9.35 甲状腺癌的 TNM 分期</td></tr>"
    "<tr><td>9.30 有效身份证件</td><td>9.36 肢体</td></tr>" + TOC_ROWS
)


def test_icon_glyph_and_sub_tags_are_removed_from_headings_and_toc_table_dropped() -> None:
    result = build_sections(ICON_HEADINGS)
    assert [" > ".join(s.path) for s in result.sections] == ["拥有的重要权益", "1.1 基本保险金额"]
    # 目录被识别成表格的部分随「条款目录」一起删掉，没有并进上一节
    assert "9.29 复利" not in result.sections[0].text
    assert "犹豫期" in result.sections[0].text


def test_real_table_after_toc_marker_is_kept() -> None:
    md = """
## 条款目录

{table}

## 1.1 基本保险金额

正文。
""".format(table="<table><tr><td>病种</td><td>定义</td></tr>" + DISEASE_ROWS)
    # 单元格里是句子的表格不是目录，不能跟着「条款目录」被删
    assert "严重脑损伤" in build_sections(md).sections[0].text


# 众安尊享e生：保险责任里先出现「（一）」，释义里才出现「一、」。按全文首次出现顺序，
# 「一、」比「（一）」深，释义条目「三十、特定疾病」挂到了「（一百）皮质基底节变性」下面（spec Q12）
PART_RESET = """
## 第二部分 保障内容

## 第六条 保险责任

## （一）一般医疗保险金

在保险期间内，被保险人在医院接受治疗的，保险人负责赔偿。

## 第七部分 释义

## 二十九、重大疾病

指被保险人初次确诊下列疾病：

## （一）恶性肿瘤——重度

指恶性细胞不受控制的进行性增长和扩散。

## （一百）皮质基底节变性

指一种慢性进展性神经变性疾病。

## 三十、特定疾病

指下列疾病。

## 八十四、语言能力完全丧失，或严重咀嚼吞咽功能障碍

指语言能力完全丧失。
"""


def test_numbering_levels_restart_in_each_part() -> None:
    assert joined_paths(PART_RESET) == [
        "第二部分 保障内容 > 第六条 保险责任 > （一）一般医疗保险金",
        "第七部分 释义 > 二十九、重大疾病",
        "第七部分 释义 > 二十九、重大疾病 > （一）恶性肿瘤——重度",
        "第七部分 释义 > 二十九、重大疾病 > （一百）皮质基底节变性",
        "第七部分 释义 > 三十、特定疾病",
        "第七部分 释义 > 八十四、语言能力完全丧失，或严重咀嚼吞咽功能障碍",
    ]


def test_unmarked_inline_four_level_heading_is_split_and_nested() -> None:
    # 信美双栏排版：4 级编号的责任名和正文挤在一行、没有 # 标记。
    # 以前不认 4 级编号，这些责任全部并进了「1.5 保险责任」（3215 字）
    md = """
## 1.5 保险责任

## 1.5.1 基本责任

1.5.1.1 首次重度疾病 被保险人经医院的专科医生初次确诊因意外伤害导致患有本合同所定义的重度疾病。

1.5.1.2 中度疾病保险 被保险人经医院的专科医生初次确诊患有本合同所定义的中度疾病。
"""
    assert joined_paths(md) == [
        "1.5 保险责任 > 1.5.1 基本责任 > 1.5.1.1 首次重度疾病",
        "1.5 保险责任 > 1.5.1 基本责任 > 1.5.1.2 中度疾病保险",
    ]


def test_bare_decimal_number_is_still_a_heading() -> None:
    # 达尔文 12 号：MinerU 把「2.3.3.4 “恶性肿瘤——重度”治疗津贴保险金」的标题文字弄丢了，只剩编号。
    # 不认的话，下面的（1）（2）（3）会挂到上一个条目「2.3.3.3 ……」下
    md = """
## 2.3.3 可选责任

## 2.3.3.3 重大疾病多次给付保险金

## （1）第二次重大疾病保险金

正文一。

## 2.3.3.4

## （1）首次治疗津贴保险金

正文二。
"""
    assert joined_paths(md) == [
        "2.3.3 可选责任 > 2.3.3.3 重大疾病多次给付保险金 > （1）第二次重大疾病保险金",
        "2.3.3 可选责任 > 2.3.3.4 > （1）首次治疗津贴保险金",
    ]


def test_bare_number_on_unmarked_line_is_body() -> None:
    # 只剩编号的标题只认 MinerU 标了 # 的行；正文里单独一行的数字不能变成章节
    md = """
## 第一条 保险金额

基本保险金额的年复利增长比例为

1.25

逐年计算。
"""
    assert joined_paths(md) == ["第一条 保险金额"]
    assert "1.25" in build_sections(md).sections[0].text


def test_numeric_table_after_toc_marker_is_kept() -> None:
    # 费率表的单元格是纯数字，不是「编号 + 标题」，不能当成目录删掉
    md = """
## 条款目录

{table}

## 1.1 基本保险金额

正文。
""".format(
        table="<table><tr><td>0.85</td><td>1.25</td></tr><tr><td>2.10</td><td>3.45</td></tr>"
        "<tr><td>4.20</td><td>5.75</td></tr></table>"
    )
    assert "1.25" in build_sections(md).sections[0].text


def test_cleaned_marked_line_that_is_body_keeps_original_text() -> None:
    # 标了 # 的句子最后进了正文：写入原文（含 <sub> 标签），正文一个字不改（D28）
    md = """
# 复星联合医联有盟重大疾病保险（互联网）条款

## 阅读指引

## <sub>条款是保险合同的重要内容，为充分保障您的权益，请仔细阅读本条款</sub>
"""
    body = build_sections(md).sections[0].text
    assert "<sub>条款是保险合同的重要内容" in body


def test_multi_column_toc_line_with_three_digit_numbers_is_removed() -> None:
    md = """
## 8.1 重度疾病

8.1.101 严重哮喘 8.1.102 严重席汉氏综合征 8.1.103 败血症

## 8.1.104 严重瑞氏综合征

指一种急性脑病。
"""
    result = build_sections(md)
    assert result.stats["removed_lines"].get("toc") == 1  # type: ignore[union-attr]
    assert joined_paths(md) == ["8.1 重度疾病 > 8.1.104 严重瑞氏综合征"]


def test_per_part_levels_are_capped_and_reported() -> None:
    from app.rag.structure import MAX_LEVEL, _line_levels

    kinds: list[str | None] = ["part", *[f"k{i}" for i in range(10)]]
    levels = {"part": 1, **{f"k{i}": min(i + 2, MAX_LEVEL) for i in range(10)}}
    assert max(lv for lv in _line_levels(kinds, levels) if lv is not None) == MAX_LEVEL
    assert build_sections(PART_RESET).stats["levels_per_part"] is True
    assert build_sections(DEEP_NUMBERS).stats["levels_per_part"] is False


def test_unmarked_bare_number_continuing_sequence_is_heading() -> None:
    # 信美：病种名被双栏排版挤走，只剩没标 # 的「8.1.95」独占一行；接着 8.1.93 往下数，算标题
    md = """
## 8.1 重度疾病

## 8.1.93 肝豆状核变性

指由于铜代谢障碍所引起的一种疾病。

8.1.95

急性肺损伤或急性呼吸窘迫综合征一种表现为无心脏衰竭的肺水肿。

8.1.200

这一行的编号跳得太远，不是标题。
"""
    assert joined_paths(md) == [
        "8.1 重度疾病 > 8.1.93 肝豆状核变性",
        "8.1 重度疾病 > 8.1.95",
    ]
    assert "8.1.200" in build_sections(md).sections[-1].text
