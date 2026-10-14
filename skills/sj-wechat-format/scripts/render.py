#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sj-wechat-format：把 Markdown 转成可直接粘贴进微信公众号后台的行内样式 HTML。

用法：
    python3 render.py <稿件.md> [--as <原稿.md>] [--h-color <色值>] [--em-color <色值>] [--open]

    --as        核对标注副本未改原稿内容，输出按原稿命名；不支持表格自动等价校验
    --h-color   分节标题颜色，默认叁斤橙 rgb(255,104,39)
    --em-color  划重点颜色，默认叁斤蓝 rgb(0,82,255)
    --open      生成后用默认浏览器打开

识别规则（AI 可先在临时副本上补标记，原稿不动）：
    1、xxx ／ 01. ／ ## xxx      独占一行 → 分节标题（标题色加粗）
    ==xxx==                       → 划重点（重点色加粗）
    **xxx**                       → 纯加粗（结构性强调，不上色）
    > xxx                         → 引用块（连续几行并成一个框）
    - xxx ／ 连续两行以上 1. xxx   → 列表
    ```                           → 代码块
    ---                           → 分割线
    ![](图.png) ／ ![[图.png]]     → 图片，转 base64 内嵌，粘贴时图跟着走
    [文字](链接)                   → 正文只留文字，链接统一列在文末
"""
import re, os, sys, base64, argparse, webbrowser
from html import escape
from pathlib import Path
from urllib.parse import unquote
from fidelity import read_markdown, verify_annotation, verify_html

FONT = ("font-family:Optima-Regular,Optima,PingFangSC-light,PingFangTC-light,"
        "&#39;PingFang SC&#39;,Cambria,Cochin,Georgia,Times,&#39;Times New Roman&#39;,serif;")
TEXT = "font-size:15px;line-height:1.75;letter-spacing:0.1em;"
GREY = "rgb(63,63,63)"


def make_style(h_color='rgb(255,104,39)', em_color='rgb(0,82,255)'):
    """叁斤风格：层级不靠字号，靠颜色和字重。正文灰、标题橙、重点蓝，全部 15px。"""
    return dict(
        body="max-width:750px;margin:0 auto;padding:22px;background-color:#ffffff;",
        p=FONT + TEXT + f"color:{GREY};margin:24px 0px;text-align:left;",
        h=f"font-weight:bold;color:{h_color};",
        em=f"font-weight:bold;color:{em_color};",
        bold=f"font-weight:bold;color:{GREY};",
        quote=FONT + TEXT + "color:rgb(90,90,90);margin:24px 0px;padding:14px 16px;"
              f"border-left:3px solid {h_color};background-color:#f7f7f7;",
        li=FONT + TEXT + f"color:{GREY};margin:8px 0px;padding-left:1.2em;text-indent:-1.2em;text-align:left;",
        code="font-family:Menlo,Consolas,monospace;font-size:13px;letter-spacing:0;"
             "background-color:#f2f2f2;color:#333333;padding:2px 6px;border-radius:3px;",
        pre="font-family:Menlo,Consolas,monospace;font-size:13px;line-height:1.6;letter-spacing:0;"
            "color:#333333;background-color:#f6f6f6;margin:24px 0px;padding:12px 14px;white-space:pre-wrap;",
        hr="border:none;border-top:1px solid rgb(229,229,229);margin:32px 0px;",
        img="max-width:100%;height:auto;display:block;margin:0 auto;border-radius:4px;",
    )


# 分节标题：「1、xxx」「01.」独占一行；「1. xxx」只有单独出现时才算标题，连续出现算列表
SECTION = re.compile(r'^\d{1,2}[、.](?:\s*\S.{0,30})?$')
OL = re.compile(r'^(\d{1,2})\.\s+(.+)$')
UL = re.compile(r'^[-*+]\s+(.+)$')
HR = re.compile(r'^(-{3,}|\*{3,}|_{3,})$')
# 图片两种写法：Obsidian 的 ![[名字.png]] 和标准 Markdown 的 ![alt](路径)
LINK = re.compile(r'(?<!!)\[([^\]]+)\]\((https?://[^)\s]+)\)')
INLINE_IMAGE = re.compile(r'!\[\[(.+?)\]\]|!\[([^\]]*)\]\((<[^>]+>|[^)]+)\)')


def find_img(name, srcdir):
    """在稿件同目录、图片附件/、attachments/、images/ 和兄弟目录（待发布↔已发布）里找图。"""
    name = unquote(name.split('|')[0].strip().strip('<>'))
    if os.path.isabs(name) and os.path.isfile(name):
        return name
    cands = [os.path.join(srcdir, name)]
    for sub in ('图片附件', 'attachments', 'images', 'assets'):
        cands.append(os.path.join(srcdir, sub, name))
    for sib in ('待发布', '已发布'):           # 稿子挪进已发布后，图可能还留在待发布/图片附件
        cands.append(os.path.join(srcdir, '..', sib, '图片附件', name))
    for c in cands:
        if os.path.isfile(c):
            return c
    return None


def embed(path):
    """图片转 base64 内嵌，复制 HTML 时图片跟着走，不依赖本地路径。"""
    with open(path, 'rb') as f:
        data = f.read()
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        mime = 'image/png'
    elif data.startswith(b'\xff\xd8\xff'):
        mime = 'image/jpeg'
    elif data.startswith((b'GIF87a', b'GIF89a')):
        mime = 'image/gif'
    elif data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        mime = 'image/webp'
    else:
        raise ValueError('不是支持的 PNG/JPEG/GIF/WebP 图片：' + os.path.basename(path))
    return f'data:{mime};base64,' + base64.b64encode(data).decode()


def esc(s):
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def inline(s, S, links):
    # 先保护代码片段，代码中的链接与强调标记不参与 Markdown 转换。
    pieces = re.split(r'(`[^`]+`)', s)
    if len(pieces) > 1:
        return ''.join(f'<code style="{S["code"]}">{esc(p[1:-1])}</code>'
                       if p.startswith('`') and p.endswith('`') else inline(p, S, links)
                       for p in pieces)
    def keep_link(m):
        links.append((m.group(1), m.group(2)))
        return m.group(1)
    s = LINK.sub(keep_link, s)
    s = esc(s)
    s = re.sub(r'==(.+?)==', lambda m: f'<strong style="{S["em"]}">{m.group(1)}</strong>', s)
    s = re.sub(r'\*\*(.+?)\*\*', lambda m: f'<strong style="{S["bold"]}">{m.group(1)}</strong>', s)
    return s


def valid_color(value):
    if re.fullmatch(r'#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?', value):
        return value
    match = re.fullmatch(r'rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)', value)
    if match and all(int(v) <= 255 for v in match.groups()):
        return value
    raise ValueError('颜色请使用 #RRGGBB、#RGB 或 rgb(0,82,255) 格式。')


def render(src, as_of=None, h_color=None, em_color=None, open_after=False, overwrite=False):
    for color in (h_color, em_color):
        if color:
            valid_color(color)
    S = make_style(**{k: v for k, v in (('h_color', h_color), ('em_color', em_color)) if v})
    ref = as_of or src
    srcdir = os.path.dirname(os.path.abspath(ref))
    source_text = Path(src).read_text(encoding='utf-8-sig')
    manuscript = read_markdown(source_text, Path(ref).stem)
    if as_of:
        original = read_markdown(Path(as_of).read_text(encoding='utf-8-sig'), Path(ref).stem)
        verify_annotation(original, manuscript)
    raw = re.sub(r'^---\n.*?\n---(?:\n|$)', '', source_text, flags=re.S)

    # 先把代码块整段摘出来（保留缩进），其余按行处理
    blocks, buf, in_code = [], [], False
    for l in raw.split('\n'):
        if l.strip().startswith('```'):
            if in_code:
                blocks.append(('CODE', '\n'.join(buf))); buf = []
            in_code = not in_code
            continue
        if in_code:
            buf.append(l)
        elif l.strip():
            # 行中图片也拆成图片块，保留两侧原文；行内代码里的图片写法不转换。
            for part in re.split(r'(`[^`]+`)', l.strip()):
                if not part:
                    continue
                if part.startswith('`') and part.endswith('`'):
                    if blocks and blocks[-1][0] == 'FRAGMENT':
                        blocks[-1] = ('FRAGMENT', blocks[-1][1] + part)
                    else:
                        blocks.append(('FRAGMENT', part))
                    continue
                start = 0
                for match in INLINE_IMAGE.finditer(part):
                    if part[start:match.start()]:
                        if blocks and blocks[-1][0] == 'FRAGMENT':
                            blocks[-1] = ('FRAGMENT', blocks[-1][1] + part[start:match.start()])
                        else:
                            blocks.append(('FRAGMENT', part[start:match.start()]))
                    if blocks and blocks[-1][0] == 'FRAGMENT':
                        blocks[-1] = ('LINE', blocks[-1][1])
                    blocks.append(('IMAGE', (match.group(1) or match.group(3), match.group(2) or '')))
                    start = match.end()
                if part[start:]:
                    if blocks and blocks[-1][0] == 'FRAGMENT':
                        blocks[-1] = ('FRAGMENT', blocks[-1][1] + part[start:])
                    else:
                        blocks.append(('FRAGMENT', part[start:]))
            if blocks and blocks[-1][0] == 'FRAGMENT':
                blocks[-1] = ('LINE', blocks[-1][1])
    if in_code and buf:
        blocks.append(('CODE', '\n'.join(buf)))

    title = Path(ref).stem
    if blocks and blocks[0][0] == 'LINE' and blocks[0][1].startswith('# '):
        blocks.pop(0)                            # 首个一级标题只进 <title>，避免和公众号标题栏重复
        title = manuscript.title
    if not blocks:
        raise ValueError('稿件没有正文，未生成 HTML。')

    links, missing, out = [], [], []
    heading = lambda t: f'<p style="{S["p"]}"><strong style="{S["h"]}">{inline(t, S, links)}</strong></p>'
    for i, (kind, l) in enumerate(blocks):
        if kind == 'IMAGE':
            name, alt = l
            name = name.strip().strip('<>')
            path = None if re.match(r'https?://', name) else find_img(name, srcdir)
            if path:
                out.append(f'<p style="{S["p"]}text-align:center;"><img src="{embed(path)}" alt="{escape(alt, quote=True)}" style="{S["img"]}"></p>')
            else:
                missing.append(name)
            continue
        if kind == 'CODE':
            out.append(f'<p style="{S["pre"]}">{esc(l)}</p>')
            continue
        prev = blocks[i - 1][1] if i > 0 and blocks[i - 1][0] == 'LINE' else ''
        nxt = blocks[i + 1][1] if i + 1 < len(blocks) and blocks[i + 1][0] == 'LINE' else ''
        in_ol = OL.match(l) and (OL.match(prev) or OL.match(nxt))
        if l.startswith('# '):                      # 后续一级标题降级为分节标题
            out.append(heading(l[2:].strip()))
        elif re.match(r'^#{2,6}\s+\S', l):
            out.append(heading(re.sub(r'^#{2,6}\s+', '', l)))
        elif HR.match(l):
            out.append(f'<hr style="{S["hr"]}">')
        elif in_ol:
            m = OL.match(l)
            out.append(f'<p style="{S["li"]}">{m.group(1)}. {inline(m.group(2), S, links)}</p>')
        elif SECTION.match(l):
            out.append(heading(l))
        elif UL.match(l):
            out.append(f'<p style="{S["li"]}"><span style="{S["h"]}">•</span> {inline(UL.match(l).group(1), S, links)}</p>')
        elif l.startswith('>'):
            out.append(('Q', inline(l.lstrip('>').strip(), S, links)))
        else:
            out.append(f'<p style="{S["p"]}">{inline(l, S, links)}</p>')

    if missing:                                     # 缺图不交付：占位文字粘进公众号就是一行「[图片：xxx]」
        print(f'❌ 这几张图没找到，没有生成 HTML：{missing}')
        print('   本地图：放进稿件同目录或 图片附件/ 再重跑；网络图：先下载到本地，把路径改成本地文件名')
        sys.exit(1)

    # 连续的引用行并成一个框：相邻行之间去掉外边距，边框和底色就连起来了
    merged, n_quote = [], 0
    for i, o in enumerate(out):
        if not isinstance(o, tuple):
            merged.append(o); continue
        first = i == 0 or not isinstance(out[i - 1], tuple)
        last = i == len(out) - 1 or not isinstance(out[i + 1], tuple)
        n_quote += first
        tweak = ('' if first else 'margin-top:0px;padding-top:4px;') + ('' if last else 'margin-bottom:0px;padding-bottom:4px;')
        merged.append(f'<p style="{S["quote"]}{tweak}">{o[1]}</p>')
    out = merged

    if links:                                       # 公众号正文外链点不开，统一列到文末备查
        out.append(f'<hr style="{S["hr"]}">')
        out.append(f'<p style="{S["p"]}"><strong style="{S["bold"]}">参考链接</strong></p>')
        for t, u in links:
            out.append(f'<p style="{S["li"]}">• {inline(t, S, [])}：{esc(u)}</p>')

    html = ('<!doctype html>\n<html lang="zh-CN">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f'<title>{esc(title)}</title>\n</head>\n<body style="{S["body"]}">\n'
            + '\n'.join(out) + '\n</body>\n</html>\n')

    # 兼容自检：只查会被公众号后台清洗或拦截的写法；正文里的网址文字不算
    checked = re.sub(r'src="data:image/[^"]+"', 'src="[img]"', html)
    tags = '\n'.join(re.findall(r'<[^>]+>', checked))
    bad = re.findall(r'<style|class=|id=|<script|@import|(?:src|href)="https?://|url\(', tags)
    if bad:
        raise ValueError(f'兼容自检失败，未写文件：{set(bad)}')
    # 正文保真与标签兼容分别验证；任何失败都发生在创建输出目录之前。
    verify_html(manuscript, html, lambda name: find_img(name, srcdir))
    base = Path(ref).stem
    outdir = os.path.join(srcdir, '公众号HTML输出', base)
    path = os.path.join(outdir, f'{base}_微信公众号版.html')
    target = Path(path)
    for candidate in (Path(srcdir) / '公众号HTML输出', Path(outdir), target):
        if candidate.is_symlink():
            raise ValueError('输出路径含符号链接，请选择普通目录和文件。')
    if target.exists():
        if target.stat().st_nlink > 1 or any(
                Path(source).exists() and target.samefile(source) for source in (src, ref)):
            raise ValueError('目标与其他文件共享内容，不能覆盖；请使用独立的 HTML 文件。')
    if os.path.exists(path) and not overwrite:
        raise ValueError('目标 HTML 已存在；确认替换后使用 --overwrite。')
    os.makedirs(outdir, exist_ok=True)
    with open(path, 'w' if overwrite else 'x', encoding='utf-8') as stream:
        stream.write(html)
    n_img = html.count('src="data:image/')
    print(f'生成 {path}')
    print(f'  段落 {len(out)}｜分节标题 {sum(1 for o in out if S["h"] in o and "•" not in o)}'
          f'｜划重点 {sum(1 for o in out if S["em"] in o)}｜引用块 {n_quote}｜内嵌图 {n_img}｜文末链接 {len(links)}')
    if n_quote > 3:
        print(f'  ⚠️ 引用块 {n_quote} 处，超过建议的 3 处上限。原稿自带的引用不算违规，AI 补标的请砍到 3 处以内')
    print('  兼容自检：' + ('通过' if not bad else f'❌ 命中禁用项 {set(bad)}'))
    if as_of:
        print('  原稿→标注副本保真：通过（文字、标题、代码、链接、图片引用及顺序）')
    else:
        print('  原稿→标注副本保真：不适用（直接渲染输入稿）')
    print('  输入稿→HTML 保真：通过（文字、标题、链接文字及目标、图片内容及顺序）')
    if open_after:
        webbrowser.open(Path(path).resolve().as_uri())
    return path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('--as', dest='as_of')
    parser.add_argument('--h-color')
    parser.add_argument('--em-color')
    parser.add_argument('--open', action='store_true', dest='open_after')
    parser.add_argument('--overwrite', action='store_true', help='明确替换已有生成 HTML')
    args = parser.parse_args()
    try:
        render(args.source, args.as_of, args.h_color, args.em_color, args.open_after, args.overwrite)
    except (OSError, ValueError) as error:
        print(f'未完成：{error}', file=sys.stderr)
        sys.exit(1)
