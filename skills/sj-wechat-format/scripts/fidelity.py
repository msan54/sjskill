"""轻量 Markdown 的内容核对；只消去本渲染器支持的格式，不抹除标点。"""
import base64
import hashlib
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote


TOKEN = re.compile(r'`([^`]+)`|!\[\[(.+?)\]\]|!\[([^\]]*)\]\((<[^>]+>|[^)]+)\)|(?<!!)\[([^\]]+)\]\((https?://[^)\s]+)\)|==(.+?)==|\*\*(.+?)\*\*')
TABLE_RULE = re.compile(r'^\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?$')


def plain(text):
    text = re.sub(r'==(.+?)==', r'\1', text)
    return re.sub(r'\*\*(.+?)\*\*', r'\1', text)


def add_text(tokens, text):
    if not text:
        return
    if tokens and tokens[-1][0] == 'text':
        tokens[-1] = ('text', tokens[-1][1] + text)
    else:
        tokens.append(('text', text))


def inline_tokens(text):
    tokens, start = [], 0
    for match in TOKEN.finditer(text):
        add_text(tokens, text[start:match.start()])
        code, wiki, alt, image, label, url, highlight, bold = match.groups()
        if code is not None:
            add_text(tokens, code)
        elif wiki is not None or image is not None:
            name = unquote((wiki or image).split('|')[0].strip().strip('<>'))
            tokens.append(('image', name, alt or ''))
        elif label is not None:
            if '(' in url:
                raise ValueError('链接目标包含未编码的括号，当前无法完整解析该 URL，未生成 HTML。')
            tokens.append(('link', plain(label), url))
        else:
            for token in inline_tokens(highlight or bold):
                if token[0] == 'text':
                    add_text(tokens, token[1])
                else:
                    tokens.append(token)
        start = match.end()
    add_text(tokens, text[start:])
    return tuple(tokens)


@dataclass
class Manuscript:
    title: str
    metadata: str
    records: list
    paragraphs: list
    links: list


def read_markdown(raw, fallback_title):
    """独立提取可支持的稿件内容与应出现的 HTML 段落，不调用渲染函数。"""
    raw = raw.lstrip('\ufeff')
    metadata = re.match(r'^---\n.*?\n---(?:\n|$)', raw, flags=re.S)
    metadata = metadata.group() if metadata else ''
    raw = raw[len(metadata):]
    title, records, paragraphs, links = fallback_title, [], [], []
    first, in_code, code = True, False, []
    for number, line in enumerate(raw.split('\n'), 1):
        stripped = line.strip()
        if stripped.startswith('```'):
            if (in_code and stripped != '```') or not re.fullmatch(r'```[\w.+-]*', stripped):
                raise ValueError(f'正文第 {number} 行的代码围栏格式不支持：结束围栏只能是三个反引号，未生成 HTML。')
            if in_code:
                value = '\n'.join(code)
                records.append(('code', value))
                paragraphs.append([('text', value)] if value else [])
                code = []
            in_code = not in_code
            first = False
            continue
        if in_code:
            code.append(line)
            continue
        if not stripped:
            continue
        outside_code = re.sub(r'`[^`]+`', '', stripped)
        if ('|' in stripped and TABLE_RULE.fullmatch(stripped)) or re.search(r'<\s*/?\s*(?:table|tr|td|th)\b', outside_code, re.I):
            raise ValueError(f'正文第 {number} 行含表格：无法自动等价校验表格或表格转列表，未生成 HTML。请逐格人工核对；不能报告正文保真全通过。')
        if first and stripped.startswith('# '):
            title_tokens = inline_tokens(stripped[2:].strip())
            if any(token[0] != 'text' for token in title_tokens):
                raise ValueError('文章标题含图片或链接，当前不能自动核对，请先明确独立的纯文字标题。')
            title = ''.join(token[1] for token in title_tokens)
            first = False
            continue
        first = False
        if re.fullmatch(r'(-{3,}|\*{3,}|_{3,})', stripped):
            continue
        # 只移除实际支持的段首格式；列表编号、标点、行内空格均保留。
        bullet = False
        if re.match(r'^#{1,6}\s+\S', stripped):
            stripped = re.sub(r'^#{1,6}\s+', '', stripped)
        elif re.match(r'^[-*+]\s+', stripped):
            stripped = re.sub(r'^[-*+]\s+', '', stripped)
            bullet = True
        elif stripped.startswith('>'):
            stripped = stripped.lstrip('>').strip()
        tokens = inline_tokens(stripped)
        records.append(('line', tokens))
        paragraph = []
        if bullet:
            add_text(paragraph, '• ')
        for token in tokens:
            if token[0] == 'image':
                if paragraph:
                    paragraphs.append(paragraph)
                    paragraph = []
                paragraphs.append([token])
            elif token[0] == 'link':
                links.append((token[1], token[2]))
                add_text(paragraph, token[1])
            else:
                add_text(paragraph, token[1])
        if paragraph:
            paragraphs.append(paragraph)
    if in_code:
        raise ValueError('代码块缺少结束围栏，无法确认正文范围，未生成 HTML。')
    return Manuscript(title, metadata, records, paragraphs, links)


def verify_annotation(original, annotated):
    for label, before, after in (
        ('标题', original.title, annotated.title),
        ('稿件元数据', original.metadata, annotated.metadata),
        ('正文、代码、链接或图片顺序', original.records, annotated.records),
    ):
        if before != after:
            raise ValueError(f'原稿→标注副本保真失败：{label}发生变化。只允许增减支持的格式标记，未生成 HTML。')


class HTMLContent(HTMLParser):
    """读取实际 HTML 的可见文本和图像，不从 HTML 注释或统计反推内容。"""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ''
        self.in_title = False
        self.in_body = False
        self.current = None
        self.paragraphs = []

    def handle_starttag(self, tag, attrs):
        if tag == 'title':
            self.in_title = True
        elif tag == 'body':
            self.in_body = True
        elif tag == 'p':
            if self.current is not None:
                raise ValueError('HTML 段落嵌套异常。')
            self.current = []
        elif tag == 'img':
            if self.current is None:
                raise ValueError('HTML 图片不在预期段落内。')
            attributes = dict(attrs)
            self.current.append(('image', attributes.get('src', ''), attributes.get('alt', '')))

    def handle_endtag(self, tag):
        if tag == 'title':
            self.in_title = False
        elif tag == 'body':
            self.in_body = False
        elif tag == 'p':
            if self.current is None:
                raise ValueError('HTML 段落结束异常。')
            self.paragraphs.append(self.current)
            self.current = None

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        elif self.current is not None:
            add_text(self.current, data)
        elif self.in_body and data.strip():
            raise ValueError('HTML 出现段落外的多余正文。')


def image_digest(uri):
    match = re.fullmatch(r'data:image/(?:png|jpeg|gif|webp);base64,([A-Za-z0-9+/=]+)', uri)
    if not match:
        raise ValueError('HTML 图片不是支持的内嵌图片。')
    try:
        return hashlib.sha256(base64.b64decode(match.group(1), validate=True)).hexdigest()
    except ValueError as error:
        raise ValueError('HTML 图片编码无效。') from error


def verify_html(manuscript, html, resolve_image):
    parsed = HTMLContent()
    parsed.feed(html)
    parsed.close()
    if parsed.current is not None or parsed.title != manuscript.title:
        raise ValueError('标注稿→HTML 保真失败：标题或段落结构不同。')
    expected = []
    for paragraph in manuscript.paragraphs:
        converted = []
        for token in paragraph:
            if token[0] == 'image':
                path = resolve_image(token[1])
                if not path:
                    raise ValueError('保真核对找不到原图：' + Path(token[1]).name)
                converted.append(('image', hashlib.sha256(Path(path).read_bytes()).hexdigest(), token[2]))
            else:
                converted.append(token)
        expected.append(converted)
    if manuscript.links:
        expected.append([('text', '参考链接')])
        expected.extend([[('text', '• ' + label + '：' + url)] for label, url in manuscript.links])
    actual = [[('image', image_digest(token[1]), token[2]) if token[0] == 'image' else token
               for token in paragraph] for paragraph in parsed.paragraphs]
    if actual != expected:
        first = next((i + 1 for i, (left, right) in enumerate(zip(expected, actual)) if left != right),
                     min(len(expected), len(actual)) + 1)
        raise ValueError(f'标注稿→HTML 保真失败：第 {first} 个输出段的文字、链接或图片不一致，未写文件。')
