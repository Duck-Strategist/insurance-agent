"""中文分词：jieba 在应用层切词，结果用空格连接后交给 PostgreSQL 的 'simple' 配置。

这样不需要在数据库里安装 zhparser 等中文分词插件，用官方 pgvector 镜像即可（spec D4）。
入库和查询必须用同一个函数分词，否则词切得不一样就匹配不上。
"""

import logging
import re

import jieba

jieba.setLogLevel(logging.WARNING)

# 保险领域词，避免被切碎（如"等待期"不应切成"等待/期"）
INSURANCE_TERMS = [
    "等待期",
    "犹豫期",
    "宽限期",
    "免赔额",
    "保险金额",
    "基本保险金额",
    "现金价值",
    "重大疾病",
    "轻度疾病",
    "中度疾病",
    "恶性肿瘤",
    "责任免除",
    "保证续保",
    "既往症",
    "特需医疗",
    "质子重离子",
    "被保险人",
    "投保人",
    "受益人",
    "保险期间",
    "交费期间",
    "意外伤害",
    "猝死",
    "伤残",
    "豁免",
]
for _term in INSURANCE_TERMS:
    jieba.add_word(_term, freq=200000)

_KEEP = re.compile(r"[\w一-鿿]")


def tokenize(text: str) -> str:
    """返回空格分隔的词串。用搜索引擎模式，长词同时产出子词，提高召回。"""
    words = (w.strip().lower() for w in jieba.lcut_for_search(text))
    return " ".join(w for w in words if w and _KEEP.search(w))
