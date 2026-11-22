"""检索评测指标（纯函数，可单测）。"""

import math
from collections.abc import Sequence


def first_hit_rank(retrieved: Sequence[object], gold: set[object]) -> int | None:
    """第一个命中标准答案的名次（从 1 开始）；没命中返回 None。"""
    for rank, item in enumerate(retrieved, start=1):
        if item in gold:
            return rank
    return None


def recall_at(ranks: Sequence[int | None], k: int) -> float:
    """命中名次 ≤ k 的题目比例。每题只要命中任意一个标准章节就算召回。"""
    if not ranks:
        return 0.0
    return sum(1 for r in ranks if r is not None and r <= k) / len(ranks)


def mrr(ranks: Sequence[int | None], k: int) -> float:
    """MRR@k：1/名次 的平均值，名次超过 k 或未命中记 0。"""
    if not ranks:
        return 0.0
    return sum(1.0 / r for r in ranks if r is not None and r <= k) / len(ranks)


def percentile(values: Sequence[float], p: float) -> float:
    """线性插值分位数（与 numpy 默认的 linear 方法一致）。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    pos = (len(ordered) - 1) * p / 100
    lo, hi = math.floor(pos), math.ceil(pos)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def longest_common_substring(a: str, b: str) -> int:
    """最长公共子串长度。用来衡量问题措辞有多像条款原文（spec Q8）。"""
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    best = 0
    for ca in a:
        cur = [0] * (len(b) + 1)
        for j, cb in enumerate(b, start=1):
            if ca == cb:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best


# ---------- 分类（路由评测） ----------


def confusion_matrix(
    gold: Sequence[str], pred: Sequence[str], labels: Sequence[str]
) -> dict[str, dict[str, int]]:
    """{标准标签: {预测标签: 次数}}，行列都按 labels 的顺序，没出现的记 0。"""
    matrix = {g: dict.fromkeys(labels, 0) for g in labels}
    for g, p in zip(gold, pred, strict=True):
        matrix[g][p] += 1
    return matrix


def per_label_scores(
    matrix: dict[str, dict[str, int]],
) -> dict[str, dict[str, float]]:
    """每个标签的精确率、召回率、F1、支持数（该标签的标准题数）。分母为 0 时记 0。"""
    labels = list(matrix)
    out: dict[str, dict[str, float]] = {}
    for label in labels:
        tp = matrix[label][label]
        predicted = sum(matrix[g][label] for g in labels)
        support = sum(matrix[label].values())
        precision = tp / predicted if predicted else 0.0
        recall = tp / support if support else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        out[label] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "support": support,
        }
    return out


def macro_f1(scores: dict[str, dict[str, float]]) -> float:
    """只平均有标准题的标签（support > 0）。"""
    used = [s["f1"] for s in scores.values() if s["support"]]
    return round(sum(used) / len(used), 4) if used else 0.0
