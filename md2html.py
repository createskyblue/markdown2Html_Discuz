#!/usr/bin/env python3
"""
Markdown → 无样式 HTML 转换器

用法:
    python md2html.py input.md                # 输出到同目录同名 .html
    python md2html.py input.md -o output.html # 指定输出路径
    python md2html.py input.md --strip-front  # 去掉 YAML front matter
    python md2html.py input.md --body-only    # 只输出 <body> 内容，不含外层文档结构

依赖:
    pip install markdown
"""

import argparse
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import markdown
from markdown.extensions import Extension
from markdown.postprocessors import Postprocessor
from markdown.preprocessors import Preprocessor


# ---------------------------------------------------------------------------
# 代码块 → <blockquote> + <br> 换行
# ---------------------------------------------------------------------------

# 匹配 codehilite 生成的 <pre class="codehilite"><code class="language-xxx">...</code></pre>
_PRE_CODE_RE = re.compile(
    r'<pre\b[^>]*>\s*<code\b[^>]*>(.*?)</code>\s*</pre>',
    re.DOTALL,
)


class CodeBlockToBlockquotePostprocessor(Postprocessor):
    """把 <pre><code> 代码块替换为 <blockquote>，行间用 <br> 分割"""

    def __init__(self, md, use_separator: bool = False):
        super().__init__(md)
        self.use_separator = use_separator

    def run(self, text: str) -> str:
        use_sep = self.use_separator

        def _replace(m: re.Match) -> str:
            inner = m.group(1)
            # BBCode 防解析（零宽空格打断 [xxx] 模式）
            inner = inner.replace('[', '[​').replace(']', '​]')

            # 把内容按行分割，每行末尾加 <br>，前导空格转 &nbsp;
            lines = inner.rstrip('\n').split('\n')
            body = ''
            for i, line in enumerate(lines):
                # 前导空格转 &nbsp; 以保留浏览器渲染时的缩进
                stripped = line.lstrip(' ')
                lead_count = len(line) - len(stripped)
                body += '&nbsp;' * lead_count + stripped
                # 非最后一行加 <br>，最后一行不留多余空行
                if i < len(lines) - 1:
                    body += '<br>\n'

            if use_sep:
                sep_open = '-' * 80 + '\n'
                sep_close = '-' * 80 + '<br>\n'
                return f'{sep_open}<br>\n<br>\n<div class="blockcode">{body}</div>\n{sep_close}'
            return f'<div class="blockcode">\n<blockquote>{body}</blockquote>\n</div>'

        return _PRE_CODE_RE.sub(_replace, text)


class CodeBlockToBlockquoteExtension(Extension):
    def __init__(self, use_separator: bool = False):
        super().__init__()
        self.use_separator = use_separator

    def extendMarkdown(self, md):
        md.postprocessors.register(
            CodeBlockToBlockquotePostprocessor(md, self.use_separator),
            'code_to_blockquote',
            25,
        )


# ---------------------------------------------------------------------------
# 标题前插入 <br>
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r'(<h[1-6]\b)', re.IGNORECASE)


class HeadingBrPostprocessor(Postprocessor):
    """每个标题标签前插入 <br> + <hr>（跳过第一个 h1）"""

    def run(self, text: str) -> str:
        replaced = [False]  # 用列表包装以在闭包中修改

        def _sub(m: re.Match) -> str:
            if not replaced[0]:
                replaced[0] = True
                return m.group(1)  # 第一个标题不加前缀
            return '<br>\n<br>\n' + m.group(1)

        return _HEADING_RE.sub(_sub, text)


class HeadingBrExtension(Extension):
    def extendMarkdown(self, md):
        md.postprocessors.register(
            HeadingBrPostprocessor(md), 'heading_br', 20
        )


# ---------------------------------------------------------------------------
# 所有 <br> 翻倍
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# YAML front matter 剥离
# ---------------------------------------------------------------------------

class StripFrontMatterPreprocessor(Preprocessor):
    """去掉 `--- ... ---` 包裹的 YAML front matter"""

    FRONT_RE = re.compile(r'^---\s*\n(.*?)\n---\s*\n', re.DOTALL)

    def run(self, lines):
        text = '\n'.join(lines)
        text = self.FRONT_RE.sub('', text, count=1)
        return text.split('\n')


class StripFrontMatterExtension(Extension):
    def extendMarkdown(self, md):
        md.preprocessors.register(StripFrontMatterPreprocessor(md), 'strip_front_matter', 100)


# ---------------------------------------------------------------------------
# 核心转换
# ---------------------------------------------------------------------------

def convert(
    input_path: Path,
    output_path: Path | None = None,
    *,
    body_only: bool = False,
    code_separator: bool = False,
) -> str:
    """读取 Markdown 文件，返回无样式 HTML 字符串。"""

    if not input_path.is_file():
        raise FileNotFoundError(f"文件不存在: {input_path}")

    text = input_path.read_text(encoding='utf-8')

    # ---- 构建扩展列表 ----
    extensions: list[str | Extension] = [
        'fenced_code',          # ``` 围栏代码块
        'codehilite',           # 代码高亮（只生成 class，不加 CSS）
        'tables',               # GFM 表格
        'toc',                  # [TOC] 目录
        CodeBlockToBlockquoteExtension(use_separator=code_separator),
        HeadingBrExtension(),               # 标题前加 <br>
    ]

    # 自动检测并剥离 YAML front matter
    extensions.append(StripFrontMatterExtension())

    # 扩展配置：代码高亮不注入 Pygments CSS
    extension_configs = {
        'codehilite': {
            'guess_lang': False,
            'use_pygments': True,       # 生成带语言 class 的 <code>
            'css_class': 'codehilite',  # 外部无 CSS，只留 class 占位
            'noclasses': False,
        },
    }

    md = markdown.Markdown(
        extensions=extensions,
        extension_configs=extension_configs,
        output_format='html',
    )

    html_body = md.convert(text)

    # 普通段落之间额外加 <br>（只针对 </p>，不影响代码块/标题/分隔符）
    html_body = html_body.replace('</p>', '</p>\n<br>')

    # 行内代码高亮 + BBCode 防解析（零宽空格打断 [xxx] 模式）
    def _highlight_inline(m: re.Match) -> str:
        content = m.group(1)
        content = content.replace('[', '[​').replace(']', '​]')
        return f'<font color="#FF2643"><font style="background-color:#FFF0F2">{content}</font></font>'
    html_body = re.sub(r'<code>(.*?)</code>', _highlight_inline, html_body)

    # 表格：thead/tbody 合并，th 内容加 <b> 粗体
    html_body = html_body.replace('<thead>', '').replace('</thead>', '')
    html_body = html_body.replace('<tbody>', '').replace('</tbody>', '')
    html_body = html_body.replace('<table>', '<table>\n<tbody>')
    html_body = html_body.replace('</table>', '</tbody>\n</table>')
    html_body = re.sub(r'<th\b[^>]*>(.*?)</th>', r'<th><b>\1</b></th>', html_body, flags=re.DOTALL)

    # 经典模式：分隔符前后间距调整
    if code_separator:
        sep_pat = '-' * 80
        # 分隔符前多加一个 <br>
        html_body = re.sub(r'(' + sep_pat + r'\n)', r'<br>\n\1', html_body)
        # 分隔符后多加一个 <br>
        html_body = html_body.replace(sep_pat + '<br>', sep_pat + '<br>\n<br>')
    else:
        # 默认模式：代码块前去掉段落带来的 <br>
        html_body = html_body.replace('<br>\n<div class="blockcode">', '\n<div class="blockcode">')

    # 标题标签替换为 <b><font>（h1→size5, h2→size4, ...）
    for level, size in [(1, 5), (2, 4), (3, 3), (4, 2), (5, 1), (6, 1)]:
        html_body = re.sub(
            rf'<h{level}\b[^>]*>(.*?)</h{level}>',
            rf'<b><font size="{size}">\1</font></b>',
            html_body,
            flags=re.DOTALL,
        )

    # ---- 组装最终输出 ----
    if body_only:
        result = html_body
    else:
        tz_plus8 = timezone(timedelta(hours=8))
        build_time = datetime.now(tz_plus8).strftime('%d %B %Y %H:%M')
        build_stamp = f'<br>\n<br>\n<hr>\n<p>build {build_time} (UTC+8)</p>'

        title = _extract_title_from_md(text) or input_path.stem
        result = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_escape_html(title)}</title>
</head>
<body>
{html_body}
{build_stamp}
</body>
</html>
"""

    # 写入文件
    if output_path is None:
        output_path = input_path.with_suffix('.html')

    output_path.write_text(result, encoding='utf-8')
    print(f"已生成: {output_path}")

    return result


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _extract_title_from_md(text: str) -> str | None:
    """从 Markdown 中提取第一个 # 级标题作为页面标题"""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('# ') and not stripped.startswith('## '):
            return stripped[2:].strip()
    return None


def _escape_html(text: str) -> str:
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description='Markdown → 无样式 HTML',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python md2html.py readme.md
  python md2html.py readme.md -o docs/readme.html
  python md2html.py blog.md --strip-front --body-only
        """.strip(),
    )

    parser.add_argument('input', type=Path, help='输入的 Markdown 文件路径')
    parser.add_argument('-o', '--output', type=Path, default=None, help='输出 HTML 文件路径（默认同目录同名 .html）')
    parser.add_argument('--body-only', action='store_true', help='只输出 body 内部 HTML，不生成 `<html>` `<head>` 等外层结构')
    parser.add_argument('--code-separator', action='store_true', help='代码块使用经典手动分隔符模式（默认用纯 div+blockquote）')

    args = parser.parse_args(argv)
    convert(
        args.input, args.output,
        body_only=args.body_only,
        code_separator=args.code_separator,
    )


if __name__ == '__main__':
    main()
