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
