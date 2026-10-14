#!/usr/bin/env python3
"""扫描中文稿件中可机械识别的模型化形状，只提供提醒，不判定发布资格。

SJskill 表达扫描工具，基于 Human Writing Skill contributors 的 MIT 代码改编。
只定位中文表达形状；不核验事实、亲历、材料数量，不给总分或 AI 概率。
内部数字仅控制提示噪声，不是诊断合格线或平台规则。

MIT License

Copyright (c) 2026 Human Writing Skill contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

from __future__ import annotations

import argparse
import collections
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path


GENRE_LABELS = {
    "auto": "自动判断",
    "wechat": "公众号长文",
    "xhs": "小红书图文",
    "voice": "口播稿",
    "short": "朋友圈／短文案",
}

PIVOT_PATTERNS = (
    re.compile(r"(?:并)?不是[^。！？\n]{0,90}而是"),
    re.compile(r"并非[^。！？\n]{0,90}而是"),
    re.compile(r"不在于[^。！？\n]{0,90}而在于"),
    re.compile(r"与其说[^。！？\n]{0,90}不如说"),
    re.compile(r"不只(?:是)?[^。！？\n]{0,90}(?:还|也)"),
    re.compile(r"表面(?:上)?[^。！？\n]{0,90}(?:其实|实际|实则)"),
    re.compile(r"看似[^。！？\n]{0,90}(?:其实|实际|实则)"),
)

MODEL_ROADS = (
    "更微妙的是",
    "还有一层",
    "只说对了一半",
    "值得注意的是",
    "需要指出的是",
    "从某种意义上说",
    "更深层次",
    "归根结底",
)

CONTEXT_JARGON = (
    "赋能",
    "抓手",
    "闭环",
    "打法",
    "拉通",
    "底层逻辑",
    "顶层设计",
    "认知跃迁",
    "降本增效",
    "内容矩阵",
    "全链路",
    "组合拳",
    "想象空间",
    "结构性机会",
    "关键命题",
    "技术底座",
    "认知增量",
    "沉淀",
    "颗粒度",
    "对齐",
    "协同",
    "链路",
    "生态位",
    "范式",
    "方法论",
    "核心变量",
)

PARAGRAPH_OPENERS = (
    "其实",
    "不过",
    "当然",
    "所以",
    "但是",
    "后来",
    "当时",
    "很多人",
    "问题是",
    "更重要的是",
    "说到这里",
)

LONG_PREFIX_PATTERNS = (
    re.compile(r"(?:^|[。！？]\s*)在[^，。！？\n]{12,70}(?:以后|之后|之前|以前|过程中|情况下|背景下)，"),
    re.compile(r"(?:^|[。！？]\s*)那些[^，。！？\n]{10,60}的[^，。！？\n]{2,30}[，。]"),
    re.compile(r"(?:^|[。！？]\s*)(?:真正|最终|最后)让[^，。！？\n]{8,70}的，是"),
)

METAPHOR_GROUPS = {
    "温度": ("降温", "升温", "冷却", "余温", "温度"),
    "战争": ("杀死", "枪响", "开火", "战场", "引爆", "弹药"),
    "建筑": ("坍塌", "崩塌", "地基", "砖头", "支柱", "废墟"),
    "仓储": ("仓库", "抽屉", "库房", "取货", "入库"),
    "道路": ("赛道", "跑道", "岔路", "十字路口", "终点线", "门票"),
    "机器身体": ("齿轮", "引擎", "发动机", "血管", "骨架", "肌肉"),
    "海洋": ("蓝海", "浪潮", "潮水", "航船", "灯塔", "彼岸"),
}

ANNEX_HEADING = re.compile(
    r"^#{1,3}\s*(?:质检单|生产档案|诊断报告|创作说明|创作备注|修改记录|内部检查)\s*$",
    re.MULTILINE,
)


@dataclass(frozen=True)
class Finding:
    category: str
    line: int
    message: str
    sample: str = ""


@dataclass(frozen=True)
class Paragraph:
    position: int
    text: str
    han: int
    sentences: int


def han_count(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text))


def line_number(text: str, position: int) -> int:
    return text.count("\n", 0, position) + 1


def sample_text(text: str, width: int = 54) -> str:
    value = re.sub(r"\s+", " ", text).strip()
    return value if len(value) <= width else value[: width - 1] + "…"


def blank_match(match: re.Match[str]) -> str:
    return "".join("\n" if char == "\n" else " " for char in match.group())


def mask_non_body(text: str) -> str:
    """屏蔽元数据、代码、链接目标和内部附录，同时保留行号位置。"""

    masked = text
    patterns = (
        re.compile(r"\A\ufeff?---\s*\n.*?\n---\s*(?:\n|\Z)", re.DOTALL),
        re.compile(r"```.*?```", re.DOTALL),
        re.compile(r"(?m)^ {0,3}~~~[^\n]*\n.*?^ {0,3}~~~[^\n]*(?:\n|\Z)", re.DOTALL),
        re.compile(r"`[^`\n]*`"),
        re.compile(r"\]\([^\n)]*\)"),
        re.compile(r"https?://[^\s)>]+"),
        re.compile(r"<[^>\n]+>"),
    )
    for pattern in patterns:
        masked = pattern.sub(blank_match, masked)

    annex = ANNEX_HEADING.search(masked)
    if annex:
        tail = masked[annex.start() :]
        masked = masked[: annex.start()] + "".join(
            "\n" if char == "\n" else " " for char in tail
        )
    return masked


def parse_paragraphs(text: str) -> list[Paragraph]:
    paragraphs: list[Paragraph] = []
    for match in re.finditer(r"(?:\A|\n\s*\n)(.*?)(?=\n\s*\n|\Z)", text, re.DOTALL):
        block = match.group(1)
        clean = re.sub(r"[>*_`]", "", block).strip()
        if not clean or clean.startswith(("#", "http", "![", "```", "|")):
            continue
        if re.match(r"^(?:[-+*]|\d+[.、])\s", clean):
            continue
        count = han_count(clean)
        if count < 4:
            continue
        sentences = max(1, len(re.findall(r"[。！？!?]", clean)))
        paragraphs.append(Paragraph(match.start(1), clean, count, sentences))
    return paragraphs


def detect_genre(requested: str, original: str, prose: str, source_name: str) -> str:
    if requested != "auto":
        return requested
    metadata = original[:500]
    type_match = re.search(
        r"(?m)^\s*(?:-\s*type/|type\s*:\s*)(公众号|小红书|口播|短文案|朋友圈)\s*$",
        metadata,
    )
    if type_match:
        return {
            "公众号": "wechat",
            "小红书": "xhs",
            "口播": "voice",
            "短文案": "short",
            "朋友圈": "short",
        }[type_match.group(1)]
    filename = Path(source_name).name if source_name != "-" else ""
    for marker, genre in (
        ("-公众号-", "wechat"),
        ("-小红书-", "xhs"),
        ("-口播-", "voice"),
        ("-短文案-", "short"),
    ):
        if marker in filename:
            return genre
    return "short" if han_count(prose) < 300 else "wechat"


def collect_pattern_matches(text: str, patterns: tuple[re.Pattern[str], ...]) -> list[re.Match[str]]:
    matches: list[re.Match[str]] = []
    for pattern in patterns:
        matches.extend(pattern.finditer(text))
    return sorted(matches, key=lambda item: item.start())


def find_non_overlapping_terms(text: str, terms: tuple[str, ...]) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    occupied: list[tuple[int, int]] = []
    for term in sorted(terms, key=len, reverse=True):
        for match in re.finditer(re.escape(term), text):
            start, end = match.span()
            if any(start < old_end and end > old_start for old_start, old_end in occupied):
                continue
            hits.append((start, term))
            occupied.append((start, end))
    return sorted(hits)


def find_heavy_de_sentences(text: str) -> list[re.Match[str]]:
    results: list[re.Match[str]] = []
    for match in re.finditer(r"[^。！？!?\n]+(?:[。！？!?]|$)", text):
        value = match.group()
        if han_count(value) >= 38 and value.count("的") >= 4:
            results.append(match)
    return results


def find_metaphor_cluster(text: str, span: int = 800):
    hits: list[tuple[int, str, str]] = []
    for group, words in METAPHOR_GROUPS.items():
        for word in words:
            hits.extend((match.start(), group, word) for match in re.finditer(re.escape(word), text))
    hits.sort()
    for index, hit in enumerate(hits):
        window = [candidate for candidate in hits[index:] if candidate[0] - hit[0] <= span]
        groups = {candidate[1] for candidate in window}
        if len(groups) >= 3:
            return window, groups
    return None


def scan(original: str, requested_genre: str, source_name: str = "-") -> tuple[str, int, list[Finding]]:
    prose = mask_non_body(original)
    total_han = han_count(prose)
    genre = detect_genre(requested_genre, original, prose, source_name)
    findings: list[Finding] = []

    pivots = collect_pattern_matches(prose, PIVOT_PATTERNS)
    pivot_limit = 3 * max(1, math.ceil(total_han / 800))
    if len(pivots) >= pivot_limit:
        first = pivots[0]
        findings.append(
            Finding(
                "翻案句密度",
                line_number(original, first.start()),
                f"共 {len(pivots)} 处，仅作重复形状线索，不能据次数判错",
                sample_text(first.group()),
            )
        )

    road_hits = [
        (match.start(), phrase)
        for phrase in MODEL_ROADS
        for match in re.finditer(re.escape(phrase), prose)
    ]
    road_hits.sort()
    road_limit = max(3, math.ceil(total_han / 900) * 2)
    if len(road_hits) >= road_limit:
        position, phrase = road_hits[0]
        findings.append(
            Finding(
                "模型路标",
                line_number(original, position),
                f"共 {len(road_hits)} 处，检查这些转折词在本文是否有明确指向",
                phrase,
            )
        )

    jargon_hits = find_non_overlapping_terms(prose, CONTEXT_JARGON)
    if jargon_hits:
        position, _ = min(jargon_hits)
        unique = "、".join(dict.fromkeys(term for _, term in sorted(jargon_hits)))
        findings.append(
            Finding(
                "语境词",
                line_number(original, position),
                f"共 {len(jargon_hits)} 处；业务本义准确时保留，换进别的文章也成立时再改",
                unique,
            )
        )

    long_prefixes = collect_pattern_matches(prose, LONG_PREFIX_PATTERNS)
    long_prefix_limit = max(2, math.ceil(total_han / 1200))
    if len(long_prefixes) > long_prefix_limit:
        first = long_prefixes[0]
        findings.append(
            Finding(
                "主干来得太晚",
                line_number(original, first.start()),
                f"长前置成分共 {len(long_prefixes)} 处，检查能否先交代人和动作",
                sample_text(first.group()),
            )
        )

    heavy_de = find_heavy_de_sentences(prose)
    heavy_de_limit = max(1, math.ceil(total_han / 1500))
    if len(heavy_de) > heavy_de_limit:
        first = heavy_de[0]
        findings.append(
            Finding(
                "重定语长句",
                line_number(original, first.start()),
                f"共 {len(heavy_de)} 句包含 4 个以上“的”，检查读者是否要回头找主干",
                sample_text(first.group()),
            )
        )

    paragraphs = parse_paragraphs(prose)
    short_streak_limit = {"wechat": 4, "xhs": 7, "voice": 7, "short": 0}[genre]
    if short_streak_limit:
        streak: list[Paragraph] = []
        for paragraph in paragraphs:
            if paragraph.han <= 24 and paragraph.sentences <= 1:
                streak.append(paragraph)
                if len(streak) >= short_streak_limit:
                    findings.append(
                        Finding(
                            "短段排队",
                            line_number(original, streak[0].position),
                            f"连续 {len(streak)} 个短促单句段，检查是否在排队喊结论",
                        )
                    )
                    break
            else:
                streak = []

    if genre == "wechat" and len(paragraphs) >= 10:
        one_sentence = sum(paragraph.sentences <= 1 for paragraph in paragraphs)
        ratio = one_sentence / len(paragraphs)
        if ratio >= 0.75:
            findings.append(
                Finding(
                    "段落模具化",
                    line_number(original, paragraphs[0].position),
                    f"可识别段落中 {ratio:.0%} 只有一句话，检查是否形成统一鼓点",
                )
            )

    opener_counts: collections.Counter[str] = collections.Counter()
    opener_positions: dict[str, int] = {}
    for paragraph in paragraphs:
        value = paragraph.text.lstrip("“‘\"（(")
        for opener in PARAGRAPH_OPENERS:
            if value.startswith(opener):
                opener_counts[opener] += 1
                opener_positions.setdefault(opener, paragraph.position)
                break
    repeated = [(opener, count) for opener, count in opener_counts.items() if count >= 4]
    if repeated:
        position = min(opener_positions[opener] for opener, _ in repeated)
        details = "、".join(f"{opener} {count} 次" for opener, count in repeated)
        findings.append(
            Finding(
                "段落开场重复",
                line_number(original, position),
                "固定开场可能替代真实衔接",
                details,
            )
        )

    metaphor_cluster = find_metaphor_cluster(prose)
    if metaphor_cluster:
        window, groups = metaphor_cluster
        position = window[0][0]
        words = "、".join(dict.fromkeys(hit[2] for hit in window))
        findings.append(
            Finding(
                "多套借喻",
                line_number(original, position),
                f"约八百字内出现 {len(groups)} 套借喻；先还原本义，再判断哪些真的缩短了解释",
                words,
            )
        )

    findings.sort(key=lambda item: (item.line, item.category))
    return genre, total_han, findings


def read_text(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="只读扫描中文表达形状，只提供线索，不判断质量或 AI 作者身份")
    parser.add_argument("path", help="Markdown／文本路径；使用 - 从标准输入读取")
    parser.add_argument(
        "--genre",
        choices=tuple(GENRE_LABELS),
        default="auto",
        help="体裁：auto、wechat、xhs、voice、short",
    )
    args = parser.parse_args()

    try:
        original = read_text(args.path)
    except (OSError, UnicodeError) as error:
        print(f"无法读取稿件：{error}", file=sys.stderr)
        return 2

    genre, total_han, findings = scan(original, args.genre, args.path)
    if total_han == 0:
        print("没有检测到正文汉字。", file=sys.stderr)
        return 2

    print(f"体裁：{GENRE_LABELS[genre]}")
    print(f"正文汉字数：{total_han}")
    print(f"机械提醒：{len(findings)} 项")

    if findings:
        for item in findings:
            sample = f"｜{item.sample}" if item.sample else ""
            print(f"- 第 {item.line} 行｜{item.category}｜{item.message}{sample}")
    else:
        print("- 未发现这份脚本覆盖的文本形状问题。")

    print("人工复核：这些表达是否妨碍本文理解；术语本义准确或有意强调时保留。")
    print("说明：本结果没有通过／失败含义，不得单独作为发布判断。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
