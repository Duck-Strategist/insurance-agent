"""M8 评测的纯函数：分类指标、路由评测汇总、端到端裁判解析与汇总、人工一致率；
以及 Langfuse 配置与路由评测用的共享函数（classify + resolve_products）。"""

import csv
import json
from pathlib import Path
from typing import Any

import pytest

from app.agent.graph import RouteDecision, classify, resolve_products
from app.core.config import LangfuseSettings
from app.eval import e2e, routing
from app.eval import metrics as M
from app.infra import tracing
from tests.test_agent import ScriptedLLM

# ---------- 分类指标 ----------


def test_confusion_matrix_and_scores() -> None:
    labels = ["a", "b", "c"]
    gold = ["a", "a", "a", "b", "b", "c"]
    pred = ["a", "a", "b", "b", "a", "a"]
    m = M.confusion_matrix(gold, pred, labels)
    assert m == {"a": {"a": 2, "b": 1, "c": 0}, "b": {"a": 1, "b": 1, "c": 0},
                 "c": {"a": 1, "b": 0, "c": 0}}  # fmt: skip
    s = M.per_label_scores(m)
    assert s["a"] == {"precision": 0.5, "recall": 0.6667, "f1": 0.5714, "support": 3}
    assert s["c"] == {"precision": 0.0, "recall": 0.0, "f1": 0.0, "support": 1}
    assert M.macro_f1(s) == round((0.5714 + 0.5 + 0.0) / 3, 4)
    # 没有标准题的标签不参与宏平均
    assert M.macro_f1({"x": {"f1": 1.0, "support": 2}, "y": {"f1": 0.0, "support": 0}}) == 1.0


# ---------- 路由：共享函数 ----------


CATALOG = [
    {"id": 1, "name": "甲医疗", "category": "medical", "document_id": 10},
    {"id": 2, "name": "乙意外", "category": "accident", "document_id": 20},
]


async def test_classify_rule_parse_failure_and_invented_ids() -> None:
    llm = ScriptedLLM(['{"intent": "claim", "product_ids": [2, 99, "1"]}', "不是 JSON"])
    assert (await classify(llm, CATALOG, "你好")).by_rule  # type: ignore[arg-type]
    d = await classify(llm, CATALOG, "骨折了乙意外能赔吗")  # type: ignore[arg-type]
    assert (d.intent, d.product_ids, d.parsed) == ("claim", [2, 1], True)
    bad = await classify(llm, CATALOG, "随便")  # type: ignore[arg-type]
    assert (bad.intent, bad.parsed) == ("clause_qa", False)
    assert "上一轮所在的业务：无" in llm.prompts_seen[0]


def test_resolve_products_only_reuses_within_same_workflow() -> None:
    none = RouteDecision("clause_qa", [], parsed=True)
    assert resolve_products(none, "clause_qa", [1]) == ([1], True)
    assert resolve_products(none, "advisor", [1]) == ([], False)
    assert resolve_products(RouteDecision("claim", [2], True), "claim", [1]) == ([2], False)
    assert resolve_products(RouteDecision("advisor", [], True), "advisor", [1]) == ([], False)


def test_routing_dataset_files_are_valid() -> None:
    root = Path(__file__).resolve().parents[2] / "data" / "eval"
    test = routing.load_items(root / "routing_test.jsonl")
    dev = routing.load_items(root / "routing_dev.jsonl")
    assert len(test) >= 200
    # 开发集和测试集不能有同一道题（spec Q16：调提示词只看开发集）
    assert not {i.question for i in test} & {i.question for i in dev}
    assert {i.intent for i in test} == set(routing.LABELS)
    assert sum(bool(i.history) for i in test) >= 50


async def test_routing_run_and_summary() -> None:
    items = [
        routing.RoutingItem(id="1", question="你好", intent="chitchat"),
        routing.RoutingItem(
            id="2", question="甲医疗等待期", intent="clause_qa", products=["甲医疗"]
        ),
        routing.RoutingItem(
            id="3",
            question="那它的犹豫期呢",
            intent="clause_qa",
            products=["甲医疗"],
            history=[("user", "甲医疗等待期"), ("assistant", "30 天")],
            last_workflow="clause_qa",
            last_products=["甲医疗"],
        ),
        routing.RoutingItem(id="4", question="天气", intent="other"),
    ]
    assert routing.unknown_products(items, CATALOG) == []  # type: ignore[arg-type]
    bad = [routing.RoutingItem(id="x", question="q", intent="claim", products=["丙"])]
    assert routing.unknown_products(bad, CATALOG) == ["x: 丙"]  # type: ignore[arg-type]
    llm = ScriptedLLM(
        [
            '{"intent": "clause_qa", "product_ids": [1]}',
            '{"intent": "clause_qa", "product_ids": []}',  # 追问：沿用上一轮产品
            '{"intent": "chitchat", "product_ids": []}',  # 错判
        ]
    )
    results = await routing.run_items(llm, CATALOG, items, history_turns=3, concurrency=1)  # type: ignore[arg-type]
    assert [r.products for r in results] == [[], ["甲医疗"], ["甲医疗"], []]
    # 多轮题的历史传给了路由
    assert "用户：甲医疗等待期" in llm.prompts_seen[1]
    m = routing.summarize(results)
    assert m["accuracy"] == 0.75 and m["multi_turn"] == {"n": 1, "accuracy": 1.0}
    assert m["product_accuracy"] == 1.0 and m["product_n"] == 2
    assert m["confusion"]["other"]["chitchat"] == 1
    assert "`4` other → **chitchat**：天气" in "\n".join(routing.render("t", 1, m, results))


# ---------- 端到端：裁判解析与汇总 ----------


PASSAGES = {
    1: (["第三条 等待期"], "本合同的等待期为 90 天，自合同生效日起计算。"),
    2: (
        ["第九条 责任免除"],
        "因下列情形之一导致被保险人身故的，我们不承担给付保险金的责任：（一）酒后驾驶。",
    ),
}


def test_parse_judgement_verifies_quotes() -> None:
    """引用有没有依据不听裁判一句话：摘录必须真的出现在第 n 段里。"""
    j = e2e.parse_judgement(
        {
            "refused": False,
            "correctness": "partial",
            "errors": ["说等待期 30 天"],
            "reason": "数字错",
            "citations": [
                {"index": 1, "claim": "等待期 90 天", "quote": "本合同的等待期为 90 天"},
                # 裁判声称有依据，但摘录在第 2 段里找不到（改写过）
                {"index": 2, "claim": "酒驾不赔", "quote": "酒驾的情况保险公司不赔"},
            ],
        },
        cited=[1, 2],
        passages=PASSAGES,
    )
    assert j.correctness == "partial" and j.parsed and j.errors == ["说等待期 30 天"]
    assert j.citations == {1: True, 2: False}
    assert j.quotes[1] == "本合同的等待期为 90 天"
    # 摘录空白、换行、表格标签不影响核对；摘录太短（不足 6 字）不算
    spaced = e2e.parse_judgement(
        {"refused": False, "correctness": "correct",
         "citations": [{"index": 1, "quote": "等待期为 90\n天，自合同"},
                       {"index": 2, "quote": "酒后"}]},
        cited=[1, 2],
        passages=PASSAGES,
    )  # fmt: skip
    assert spaced.citations == {1: True, 2: False}
    # 编号不在回答模型看到的段落里、裁判漏判、正确性非法：都按最不利记
    bad = e2e.parse_judgement(
        {"correctness": "great", "citations": [{"index": 7, "quote": "等待期为 90 天"}]},
        cited=[3, 7],
        passages=PASSAGES,
    )
    assert (bad.correctness, bad.parsed) == ("incorrect", False)
    assert bad.citations == {3: False, 7: False}


def test_quote_matching_tolerates_format_but_not_paraphrase() -> None:
    """来自开发集（v1 抽检的 17 题）里 v2 核对失败的真实情况。"""
    passage = (
        "本合同首个保单年度健康管理系数等于 100%。被保险人参加本公司指定的合作医疗机构"
        "<sup>11</sup>或服务供应商提供的健康管理服务。每种轻度疾病限给付一次。"
        "本合同轻度疾病保险金累计给付次数以 4 次为限。"
    )
    q = e2e.quote_in_passage
    assert q("被保险人参加本公司指定的合作医疗机构或服务供应商", passage)  # 上标被省略
    assert q("首个保单年度健康管理系数等于 100%。\\n本合同轻度疾病保险金累计给付次数", passage)
    # 两句拼接
    assert q("每种轻度疾病限给付一次。本合同轻度疾病保险金累计给付次数以4次为限", passage)
    assert q("健康管理系数等于100%;每种轻度疾病限给付一次", passage)  # 半角标点
    assert not q("被保险人参加了任意机构的服务", passage)  # 改写过的
    assert not q("每种轻度疾病限给付一次。累计不限次数的说法", passage)  # 一句对一句不对
    assert not q("等于", passage)  # 太短


def test_judge_prompt_shows_all_passages_with_answer_numbering() -> None:
    block = e2e.passages_block(PASSAGES)
    assert block.index("[1] 第三条 等待期") < block.index("[2] 第九条 责任免除")
    assert e2e.passages_block({}) == "（没有检索到条款原文）"
    prompt = e2e.JUDGE_USER.format(question="q", reference_block="r", passages=block, answer="a")
    assert "回答模型看到的全部条款原文" in prompt and "quote" in prompt


def result(
    id_: str, answerable: bool, correctness: str, refused: bool, cites: dict[int, bool]
) -> e2e.E2EResult:
    item = e2e.E2EItem(id=id_, question="q" + id_, file_name="f.pdf", answerable=answerable)
    j = e2e.Judgement(refused, correctness, cites, "r", True)  # type: ignore[arg-type]
    citations = [{"index": i, "section_path": ["s"], "parent_content": "原文"} for i in cites]
    return e2e.E2EResult(item, "答", citations, j, 1000.0)


def test_e2e_summary() -> None:
    rs = [
        result("1", True, "correct", False, {1: True, 2: True}),
        result("2", True, "partial", False, {1: False}),
        result("3", True, "incorrect", True, {}),  # 该答却拒答
        result("4", True, "correct", False, {}),  # 答对了但没引用
        result("5", False, "correct", True, {}),
        result("6", False, "incorrect", False, {1: False}),  # 编造
    ]
    m = e2e.summarize(rs)
    a = m["answerable"]
    assert a["correctness"] == round((1 + 0.5 + 0 + 1) / 4, 4)
    assert (a["correct_rate"], a["false_refusal_rate"]) == (0.5, 0.25)
    assert a["uncited_rate"] == round(1 / 3, 4)
    # 引用忠实度只统计有答案题里的引用：3 处里 2 处有依据
    assert (m["citation_precision"], m["citations_total"]) == (round(2 / 3, 4), 3)
    assert m["unanswerable"] == {"n": 2, "refusal_rate": 0.5, "hallucination_rate": 0.5}


def test_blind_csv_hides_judge(tmp_path: Path) -> None:
    rs = [result("a01", True, "partial", False, {1: True})]
    path = tmp_path / "blind.csv"
    e2e.write_human_csv(path, 9, rs, {}, blind=True)
    row = next(csv.DictReader(path.open(encoding="utf-8-sig")))
    assert (row["judge_correctness"], row["judge_refused"], row["judge_citations"]) == ("", "", "")


def test_human_sample_csv_and_agreement(tmp_path: Path) -> None:
    rs = [result(f"a{i:02d}", True, "correct", False, {1: True}) for i in range(20)]
    rs += [result(f"n{i}", False, "correct", True, {}) for i in range(5)]
    sample = e2e.sample_for_human(rs, seed=1)
    assert sum(r.item.answerable for r in sample) == 4 and len(sample) == 5
    assert e2e.sample_for_human(rs, seed=1) == sample  # 固定种子可复现
    # 排除看过的题后再抽：不重复，每层题数仍按全部题目的比例
    again = e2e.sample_for_human(rs, seed=1, exclude=frozenset(r.item.id for r in sample))
    assert len(again) == 5 and not {r.item.id for r in again} & {r.item.id for r in sample}

    path = tmp_path / "h.csv"
    e2e.write_human_csv(path, 7, sample, {r.item.id: "标准原文" for r in sample})
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")  # Excel 能识别 UTF-8
    with pytest.raises(ValueError, match="还没有人工标注"):
        e2e.agreement(e2e.read_human_csv(path)[1])

    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    labels = [("correct", "否", "是"), ("partial", "否", "否"), ("correct", "否", "是"),
              ("correct", "否", "是"), ("correct", "是", "")]  # fmt: skip
    for row, (c, r, ok) in zip(rows, labels, strict=True):
        row.update(human_correctness=c, human_refused=r, human_citations_ok=ok)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=e2e.HUMAN_FIELDS)
        w.writeheader()
        w.writerows(rows)
    run_id, back = e2e.read_human_csv(path)
    got = e2e.agreement(back)
    # 同一批人工标注和另一次运行的裁判比：裁判结论按题目 id 从那次运行的逐题结果里取
    details = [r.as_detail() for r in rs]
    for d in details:
        d["judge"]["correctness"] = "partial"
    other = e2e.agreement(e2e.with_judge(back, details))
    assert other["correctness_agreement"] == 0.2  # 人工只有 1 题是 partial
    with pytest.raises(ValueError, match="这次运行里没有这些题"):
        e2e.with_judge(back, details[:1])
    assert run_id == 7 and got["n"] == 5
    assert got["correctness_agreement"] == 0.8
    assert got["refused_agreement"] == 1.0
    assert got["citations_agreement"] == 0.75  # 第 2 题裁判说有依据、人工说没有


def test_cohen_kappa() -> None:
    assert e2e.cohen_kappa(["a", "b", "a", "b"], ["a", "b", "a", "b"], ["a", "b"]) == 1.0
    assert e2e.cohen_kappa(["a", "a"], ["a", "a"], ["a", "b"]) is None  # 只有一个标签
    assert e2e.cohen_kappa(["a", "b", "a", "b"], ["a", "a", "b", "b"], ["a", "b"]) == 0.0


def test_e2e_dataset_file_is_valid() -> None:
    root = Path(__file__).resolve().parents[2] / "data" / "eval"
    rows = [json.loads(x) for x in (root / "e2e_clause.jsonl").read_text("utf-8").splitlines()]
    items = [e2e.E2EItem.model_validate(r) for r in rows]
    assert len({i.id for i in items}) == len(items)
    assert all(i.gold for i in items if i.answerable)
    assert sum(not i.answerable for i in items) >= 10


# ---------- Langfuse ----------


def test_langfuse_settings_and_disabled_tracing() -> None:
    assert not LangfuseSettings().enabled()
    assert not LangfuseSettings(public_key="pk", secret_key="  ").enabled()  # type: ignore[arg-type]
    assert LangfuseSettings(public_key="pk", secret_key="sk").enabled()  # type: ignore[arg-type]
    # 没启用时：不创建客户端，config 为空，flush 不报错
    assert tracing.init_tracing(LangfuseSettings()) is False
    assert tracing.trace_config("chat", user_id=1, session_id="t") == {}
    tracing.flush_traces()


def test_trace_config_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    created: dict[str, Any] = {}

    class FakeClient:
        def __init__(self, **kw: Any) -> None:
            created.update(kw)

        def flush(self) -> None: ...

        def shutdown(self) -> None:
            created["shutdown"] = True

    monkeypatch.setattr(tracing, "Langfuse", FakeClient)
    monkeypatch.setattr(tracing, "CallbackHandler", lambda public_key: ("handler", public_key))
    try:
        settings = LangfuseSettings(public_key="pk", secret_key="sk", host="http://lf:3000")  # type: ignore[arg-type]
        assert tracing.init_tracing(settings, "eval")
        assert created["host"] == "http://lf:3000" and created["environment"] == "eval"
        cfg = tracing.trace_config("chat", user_id=5, session_id="th-1", tags=["chat"])
        assert cfg["callbacks"] == [("handler", "pk")]
        assert cfg["metadata"] == {
            "langfuse_trace_name": "chat",
            "langfuse_tags": ["chat"],
            "langfuse_user_id": "5",
            "langfuse_session_id": "th-1",
        }
    finally:
        tracing.shutdown_tracing()
    assert created["shutdown"] and not tracing.tracing_enabled()


async def test_with_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx
    import openai

    from app.eval import retry

    async def no_sleep(_: float) -> None: ...

    monkeypatch.setattr(retry.asyncio, "sleep", no_sleep)
    calls: list[int] = []

    async def flaky() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("peer closed connection")
        return "ok"

    assert await retry.with_retries(flaky, label="q1") == "ok" and len(calls) == 3

    async def always() -> str:
        raise RuntimeError("down")

    with pytest.raises(RuntimeError):
        await retry.with_retries(always, label="q2")

    denied = openai.AuthenticationError(
        "bad key",
        response=httpx.Response(401, request=httpx.Request("POST", "http://x")),
        body=None,
    )
    calls.clear()

    async def auth() -> str:
        calls.append(1)
        raise denied

    with pytest.raises(openai.AuthenticationError):
        await retry.with_retries(auth, label="q3")
    assert len(calls) == 1  # 认证错误不重试
