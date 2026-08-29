"""把 Markdown / LaTeX 文本导出为 Word(.docx)。

纯 python-docx 实现，不依赖 pandoc（原先 pandoc 优先的双路径已移除：
requirements 装的是 pypandoc 而非 pypandoc-binary，用户机上主路径从不可达）。

支持：
- 公式 $...$ 行内 / $$...$$ 独立成行 → OMML，Word 里可双击编辑
- 独立公式右对齐 (1)(2) 编号（制表位实现，公式仍居中）
- Markdown 表格（首行表头 + 分隔行）；表前 `Caption: 表题` 行生成 APA 表号/表题，
  表后 `Note: 注释` 行生成 APA Note. 注释
- 图片 ![alt](路径 或 data:image/...;base64,...)，alt 自动成图注
- 6 级标题、有序/无序列表、引用、围栏代码块、水平线
- 行内 **粗体** *斜体* `等宽`
- A4 页面 + 页边距 + 页脚页码，可选封面

未做（留待交叉引用一起补）：编号是字面数字而非 SEQ 域，
故在 Word 里手动插入新公式不会自动重排后续编号。
"""
import base64
import io
import os
import re

OMML_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"

# 字体：HiMCM 只投美赛区，评委读英文，故用纯西文字体族。
# 正文 Times New Roman（衬线，正式）；标题 Arial（无衬线，层级对比清晰）。
_FONT_BODY = "Times New Roman"
_FONT_HEAD = "Arial"
_FONT_MONO = "Consolas"


# ── 公式：LaTeX → MathML → OMML ─────────────────────────────────────────────

def _repair_omml(xml: str) -> str:
    """修 mathml2omml（停更于 2019）的 bug：走 groupChr 分支时开
    <m:groupChrPr> 却用 </m:groupChr> 收尾，产出的 XML 不合法。

    实测 9 个重音符，命中 3 个：\\bar \\vec \\widetilde。
    正常的：\\hat \\tilde \\overline \\dot \\ddot \\widehat。
    \\bar 是建模论文最常用记号之一（样本均值），不修则这些公式静默丢失。
    """
    out, pos = [], 0
    while True:
        i = xml.find("<m:groupChrPr>", pos)
        if i == -1:
            out.append(xml[pos:])
            return "".join(out)
        j = xml.find("</m:groupChr>", i)
        if j == -1:
            out.append(xml[pos:])
            return "".join(out)
        out.append(xml[pos:j])
        out.append("</m:groupChrPr>")
        pos = j + len("</m:groupChr>")


def _latex_to_omml_element(latex: str, display: bool = False):
    """LaTeX 片段 → OMML lxml 元素；失败返回 None（调用方回落为纯文本）。"""
    try:
        import latex2mathml.converter
        import mathml2omml
        from lxml import etree
    except ImportError:
        return None
    try:
        mathml = latex2mathml.converter.convert(latex.strip())
        xml = _repair_omml(mathml2omml.convert(mathml))
        if isinstance(xml, bytes):
            xml = xml.decode("utf-8")
        if "xmlns:m" not in xml:
            xml = xml.replace("<m:oMath>", f'<m:oMath xmlns:m="{OMML_NS}">', 1)
        el = etree.fromstring(xml.encode("utf-8"))
        if display:
            # 独立公式：包一层 oMathPara，Word 会居中成段
            wrapper = etree.Element(f"{{{OMML_NS}}}oMathPara", nsmap={"m": OMML_NS})
            wrapper.append(el)
            return wrapper
        return el
    except Exception:
        return None


# ── 行内文本：粗体/斜体/等宽/公式 ────────────────────────────────────────────

_INLINE_RE = re.compile(
    r"(\$[^$\n]+?\$)"            # 行内公式
    r"|(\*\*.+?\*\*)"            # 粗体
    r"|(`[^`]+?`)"               # 等宽
    r"|(?<!\*)(\*[^*\n]+?\*)"    # 斜体
    r"|(\[.+?\]\(.+?\))"         # 链接（取文字）
)


def _add_inline(para, text: str, font: str = _FONT_BODY):
    """把一行 Markdown 文本按行内标记切开，逐段加进段落。"""
    if not text:
        return
    pos = 0
    for m in _INLINE_RE.finditer(text):
        if m.start() > pos:
            _plain_run(para, text[pos:m.start()], font)
        tok = m.group(0)
        if tok.startswith("$"):
            el = _latex_to_omml_element(tok[1:-1])
            if el is not None:
                para._p.append(el)
            else:
                _plain_run(para, tok, font)     # 转不了就保留原样，不静默丢
        elif tok.startswith("**"):
            _plain_run(para, tok[2:-2], font).bold = True
        elif tok.startswith("`"):
            _plain_run(para, tok[1:-1], _FONT_MONO)
        elif tok.startswith("*"):
            _plain_run(para, tok[1:-1], font).italic = True
        else:                                   # 链接：只留可见文字
            _plain_run(para, re.match(r"\[(.+?)\]", tok).group(1), font)
        pos = m.end()
    if pos < len(text):
        _plain_run(para, text[pos:], font)


def _plain_run(para, text: str, font: str = _FONT_BODY):
    """加一个 run 并显式设字体（同时设 ascii/hAnsi，避免主题字体覆盖）。"""
    from docx.oxml.ns import qn
    r = para.add_run(text)
    r.font.name = font
    rpr = r._element.get_or_add_rPr()
    rf = rpr.find(qn("w:rFonts"))
    if rf is None:
        rf = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(rf)
    rf.set(qn("w:ascii"), font)
    rf.set(qn("w:hAnsi"), font)
    return r


# ── 表格 ────────────────────────────────────────────────────────────────────

_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def _split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _add_display_equation(doc, latex: str, number: int | None):
    """独立公式成段。number 非空时右侧加 (1) (2) 编号。

    用制表位实现学术惯例：公式居中、编号右对齐到右边距。
    这样公式的居中不受编号宽度影响——直接写空格会把公式推偏。
    """
    from docx.shared import Cm
    from docx.enum.text import WD_TAB_ALIGNMENT, WD_ALIGN_PARAGRAPH

    p = doc.add_paragraph()
    pf = p.paragraph_format

    if number is None:
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    else:
        # 正文宽度 = A4 21cm - 左右边距 3.17cm ×2 = 14.66cm
        usable = Cm(14.66)
        pf.tab_stops.add_tab_stop(Cm(7.33), WD_TAB_ALIGNMENT.CENTER)
        pf.tab_stops.add_tab_stop(usable, WD_TAB_ALIGNMENT.RIGHT)
        p.add_run().add_tab()          # 跳到居中制表位

    el = _latex_to_omml_element(latex, display=(number is None))
    if el is not None:
        p._p.append(el)
    else:
        _plain_run(p, f"$${latex}$$")   # 转不了保留原文，不静默丢

    if number is not None:
        run = p.add_run()
        run.add_tab()                  # 跳到右对齐制表位
        _plain_run(p, f"({number})")
    return p


def _set_cell_borders(cell, **edges):
    """按边设置单元格边框。edges 形如 top={"sz":8,"val":"single"}。"""
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    tcPr = cell._tc.get_or_add_tcPr()
    borders = tcPr.find(qn("w:tcBorders"))
    if borders is None:
        borders = OxmlElement("w:tcBorders")
        tcPr.append(borders)
    for edge in ("top", "bottom", "left", "right"):
        spec = edges.get(edge)
        tag = f"w:{edge}"
        el = borders.find(qn(tag))
        if el is None:
            el = OxmlElement(tag)
            borders.append(el)
        if spec is None:                       # 显式画“无边框”
            el.set(qn("w:val"), "nil")
            continue
        el.set(qn("w:val"), spec.get("val", "single"))
        el.set(qn("w:sz"), str(spec.get("sz", 8)))
        el.set(qn("w:color"), spec.get("color", "000000"))
        el.set(qn("w:space"), "0")


def _add_table(doc, rows: list[list[str]], *,
               table_no: int | None = None, caption: str | None = None):
    """rows[0] 当表头。排成三线表 —— 这正是 APA 第七版的表格规范：
    只有顶线、表头下线、底线，无竖线（Table Grid 带竖线，不合规）。

    table_no 非空时在表上方插入 APA 表号行（'Table N' 加粗、左对齐）；
    caption 非空时在表号下方插入斜体表题（左对齐）。
    Note. 注释由调用方在表下方通过 _add_table_note() 添加。
    """
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.shared import Pt

    if table_no is not None:
        p_num = doc.add_paragraph()
        p_num.paragraph_format.space_before = Pt(6)
        p_num.paragraph_format.space_after = Pt(0)
        r_num = _plain_run(p_num, f"Table {table_no}")
        r_num.bold = True
        if caption:
            p_cap = doc.add_paragraph()
            p_cap.paragraph_format.space_before = Pt(0)
            p_cap.paragraph_format.space_after = Pt(2)
            r_cap = _plain_run(p_cap, caption)
            r_cap.italic = True

    ncol = max(len(r) for r in rows)
    t = doc.add_table(rows=0, cols=ncol)
    try:
        t.style = "Table Normal"               # 无任何边框，由下面逐边补
    except KeyError:
        pass
    t.alignment = WD_TABLE_ALIGNMENT.CENTER

    THICK = {"sz": 12, "val": "single"}        # 顶线/底线较粗
    THIN = {"sz": 6, "val": "single"}          # 表头下线较细
    last = len(rows) - 1

    for ri, row in enumerate(rows):
        cells = t.add_row().cells
        for ci in range(ncol):
            cell = cells[ci]
            cell.paragraphs[0].text = ""
            para = cell.paragraphs[0]
            para.paragraph_format.space_before = Pt(2)
            para.paragraph_format.space_after = Pt(2)
            para.paragraph_format.line_spacing = 1.0
            _add_inline(para, row[ci] if ci < len(row) else "")
            if ri == 0:
                for p in cell.paragraphs:
                    for r in p.runs:
                        r.bold = True
                _set_cell_borders(cell, top=THICK, bottom=THIN)
            elif ri == last:
                _set_cell_borders(cell, bottom=THICK)
            else:
                _set_cell_borders(cell)        # 中间行：四边全无
    return t


def _add_table_note(doc, text: str):
    """APA 7th 表注：'Note. '斜体 + 正文，表格下方紧跟。"""
    from docx.shared import Pt
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(0)
    r_label = _plain_run(p, "Note. ")
    r_label.italic = True
    _add_inline(p, text)

_IMG_RE = re.compile(r"^\s*!\[(?P<alt>[^\]]*)\]\((?P<src>[^)]+)\)\s*$")


def _add_image(doc, src: str, alt: str, base_dir: str | None):
    """支持本地路径和 data:image/...;base64,... 内联图。失败则写一行占位文字。"""
    from docx.shared import Inches, Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    stream = None
    try:
        if src.startswith("data:"):
            b64 = src.split(",", 1)[1]
            stream = io.BytesIO(base64.b64decode(b64))
        else:
            path = src if os.path.isabs(src) else os.path.join(base_dir or ".", src)
            if os.path.exists(path):
                stream = open(path, "rb")
        if stream is None:
            doc.add_paragraph(f"[图片缺失: {alt or src}]")
            return
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.add_run().add_picture(stream, width=Inches(5.5))
        if alt:                                  # alt 当图注
            cap = doc.add_paragraph()
            cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
            r = cap.add_run(alt)
            r.italic = True
            r.font.size = Pt(9)
    except Exception:
        doc.add_paragraph(f"[图片无法嵌入: {alt or src}]")
    finally:
        if stream is not None and not isinstance(stream, io.BytesIO):
            stream.close()


# ── 页面设置 / 页码 / 封面 ───────────────────────────────────────────────────

def _setup_page(doc):
    """A4 + 2.54cm 页边距 + 正文样式（小四号，1.5 倍行距）。"""
    from docx.shared import Cm, Pt
    from docx.oxml.ns import qn
    for s in doc.sections:
        s.page_width, s.page_height = Cm(21.0), Cm(29.7)
        s.top_margin = s.bottom_margin = Cm(2.54)
        s.left_margin = s.right_margin = Cm(3.17)

    st = doc.styles["Normal"]
    st.font.name = _FONT_BODY
    st.font.size = Pt(12)
    rf = st.element.rPr.rFonts
    rf.set(qn("w:ascii"), _FONT_BODY)
    rf.set(qn("w:hAnsi"), _FONT_BODY)
    st.paragraph_format.line_spacing = 1.5
    st.paragraph_format.space_after = Pt(0)

    # 标题族统一 Arial，并去掉 Word 默认的蓝色
    from docx.shared import RGBColor
    for lvl in range(1, 7):
        try:
            hs = doc.styles[f"Heading {lvl}"]
        except KeyError:
            continue
        hs.font.name = _FONT_HEAD
        hs.font.color.rgb = RGBColor(0, 0, 0)
        hs.font.bold = True
        if hs.element.rPr is not None and hs.element.rPr.rFonts is not None:
            hrf = hs.element.rPr.rFonts
            hrf.set(qn("w:ascii"), _FONT_HEAD)
            hrf.set(qn("w:hAnsi"), _FONT_HEAD)


def _add_page_number_footer(doc):
    """页脚居中页码。python-docx 无 API，需插 PAGE 域代码。"""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    for s in doc.sections:
        p = s.footer.paragraphs[0] if s.footer.paragraphs else s.footer.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run()
        for tag, attr, text in (
            ("w:fldChar", ("w:fldCharType", "begin"), None),
            ("w:instrText", ("xml:space", "preserve"), " PAGE "),
            ("w:fldChar", ("w:fldCharType", "end"), None),
        ):
            el = OxmlElement(tag)
            el.set(qn(attr[0]), attr[1])
            if text:
                el.text = text
            run._r.append(el)


def _add_cover(doc, title: str, subtitle: str = "", meta: str = ""):
    """封面：标题居中 + 可选副标题/信息 + 分页。"""
    from docx.shared import Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    for _ in range(6):
        doc.add_paragraph()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = _plain_run(p, title)
    r.bold = True
    r.font.size = Pt(26)
    if subtitle:
        p2 = doc.add_paragraph()
        p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _plain_run(p2, subtitle).font.size = Pt(16)
    if meta:
        for _ in range(3):
            doc.add_paragraph()
        for line in meta.splitlines():
            pm = doc.add_paragraph()
            pm.alignment = WD_ALIGN_PARAGRAPH.CENTER
            _plain_run(pm, line).font.size = Pt(12)
    doc.add_page_break()


# ── Markdown 主解析 ─────────────────────────────────────────────────────────

def _clean_markdown(md: str) -> str:
    """预处理 Markdown：移除 LLM 自己标注的公式编号和箭头占位符。"""
    # 1. 移除独立公式后面的手动编号：$$...$$ (11) → $$...$$
    md = re.sub(r"(\$\$[^$]+?\$\$)\s*\((\d+)\)", r"\1", md)
    # 2. 移除独立行的箭头占位符（通常是制表位或对齐标记）
    md = re.sub(r"^\s*→\s*$", "", md, flags=re.MULTILINE)
    # 3. 移除公式行内的独立箭头（但保留箭头符号本身在公式内的合法使用）
    md = re.sub(r"→\s*\((\d+)\)", "", md)  # → (13) 这种模式直接删

    # 4. 拆分 \begin{aligned}...\end{aligned} 为多个独立公式
    # latex2mathml 不支持 aligned 环境，需要拆成单行
    def split_aligned(match):
        content = match.group(1)
        # 按 \\ 分行
        lines = re.split(r'\\\\', content)
        result = []
        for line in lines:
            line = line.strip()
            if line and not line.startswith('&'):  # 跳过空行和对齐符
                # 移除对齐符 &
                line = line.replace('&', '')
                result.append(f"$$\n{line}\n$$\n")
        return '\n'.join(result)

    md = re.sub(
        r'\$\$\s*\\begin\{aligned\}(.+?)\\end\{aligned\}\s*\$\$',
        split_aligned,
        md,
        flags=re.DOTALL
    )

    # 5. 拆分 \begin{cases}...\end{cases} 为文本描述
    # cases 通常是分段函数，转成 "f(x) = ... for condition 1, and f(x) = ... for condition 2"
    def split_cases(match):
        content = match.group(1)
        # 简单处理：按 \\ 分行，每行变成独立公式
        lines = re.split(r'\\\\', content)
        result = []
        for i, line in enumerate(lines):
            line = line.strip()
            if line:
                # 移除 & 分隔符，保留公式和条件
                parts = line.split('&')
                if len(parts) == 2:
                    result.append(f"$$\n{parts[0].strip()}\n$$\nfor {parts[1].strip()}")
                else:
                    result.append(f"$$\n{line}\n$$")
        return '\n\n'.join(result)

    md = re.sub(
        r'\$\$\s*\\begin\{cases\}(.+?)\\end\{cases\}\s*\$\$',
        split_cases,
        md,
        flags=re.DOTALL
    )

    return md


def _render_markdown(doc, md: str, base_dir: str | None = None,
                     number_equations: bool = True):
    from docx.shared import Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    md = _clean_markdown(md)  # 预处理
    lines = md.replace("\r\n", "\n").split("\n")
    i, n = 0, len(lines)
    eq_no = 0                      # 独立公式计数器，(1)(2)(3)… 全文连续
    tbl_no = 0                     # 表号计数器，Table 1 / Table 2 …
    pending_caption: str | None = None   # Caption: 行暂存，遇表格时消费

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # 围栏代码块
        if stripped.startswith("```"):
            i += 1
            buf = []
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            p = doc.add_paragraph()
            p.paragraph_format.line_spacing = 1.0
            r = p.add_run("\n".join(buf))
            r.font.name = _FONT_MONO
            r.font.size = Pt(10)
            continue

        # $$ 独立公式 $$（同行闭合或跨行）
        if stripped.startswith("$$"):
            body = stripped[2:]
            if body.endswith("$$") and len(stripped) > 4:
                latex = body[:-2]
                i += 1
            else:
                buf = [body] if body else []
                i += 1
                while i < n and not lines[i].strip().endswith("$$"):
                    buf.append(lines[i])
                    i += 1
                if i < n:
                    tail = lines[i].strip()[:-2]
                    if tail:
                        buf.append(tail)
                    i += 1
                latex = "\n".join(buf)
            if number_equations:
                eq_no += 1
                _add_display_equation(doc, latex, eq_no)
            else:
                _add_display_equation(doc, latex, None)
            continue

        # Caption: 行（APA 表号/表题）：存储，遇到紧随的表格时消费
        if re.match(r"^Caption:\s*\S", stripped):
            pending_caption = stripped[len("Caption:"):].strip()
            i += 1
            continue

        # 表格：当前行像表格行 且 下一行是分隔行
        if "|" in stripped and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1]):
            rows = [_split_row(stripped)]
            i += 2
            while i < n and "|" in lines[i] and lines[i].strip():
                rows.append(_split_row(lines[i]))
                i += 1
            tbl_no += 1
            _add_table(doc, rows, table_no=tbl_no, caption=pending_caption)
            pending_caption = None
            if i < n:
                m_note = re.match(r"^Note:\s*(.+)$", lines[i].strip())
                if m_note:
                    _add_table_note(doc, m_note.group(1))
                    i += 1
                else:
                    doc.add_paragraph()
            else:
                doc.add_paragraph()
            continue

        # 图片独占一行
        m = _IMG_RE.match(line)
        if m:
            _add_image(doc, m.group("src"), m.group("alt"), base_dir)
            i += 1
            continue

        # 水平线
        if re.match(r"^\s*([-*_])\s*(\1\s*){2,}$", line):
            doc.add_paragraph("─" * 40).alignment = WD_ALIGN_PARAGRAPH.CENTER
            i += 1
            continue

        # 标题 1-6 级
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            h = doc.add_heading(level=len(m.group(1)))
            h.text = ""
            _add_inline(h, m.group(2).strip(), font=_FONT_HEAD)
            i += 1
            continue

        # 引用
        if stripped.startswith(">"):
            p = doc.add_paragraph(style="Intense Quote") if "Intense Quote" in [
                s.name for s in doc.styles
            ] else doc.add_paragraph()
            _add_inline(p, stripped.lstrip("> ").strip())
            i += 1
            continue

        # 无序 / 有序列表（按缩进给 2 级）
        m = re.match(r"^(\s*)[-*+]\s+(.*)$", line)
        if m:
            lvl = min(len(m.group(1)) // 2, 1)
            style = "List Bullet" if lvl == 0 else "List Bullet 2"
            _add_inline(_safe_para(doc, style), m.group(2))
            i += 1
            continue
        m = re.match(r"^(\s*)\d+[.)]\s+(.*)$", line)
        if m:
            lvl = min(len(m.group(1)) // 2, 1)
            style = "List Number" if lvl == 0 else "List Number 2"
            _add_inline(_safe_para(doc, style), m.group(2))
            i += 1
            continue

        # 空行
        if not stripped:
            i += 1
            continue

        # 普通段落
        p = doc.add_paragraph()
        p.paragraph_format.first_line_indent = Pt(24)   # 中文习惯首行缩进
        _add_inline(p, stripped)
        i += 1


def _safe_para(doc, style: str):
    """样式不存在时退回默认样式，避免 KeyError。"""
    try:
        return doc.add_paragraph(style=style)
    except KeyError:
        return doc.add_paragraph()


# ── 对外入口 ────────────────────────────────────────────────────────────────

def to_docx(
    content: str,
    source_fmt: str = "markdown",
    *,
    cover_title: str = "",
    cover_subtitle: str = "",
    cover_meta: str = "",
    page_numbers: bool = True,
    number_equations: bool = True,
    base_dir: str | None = None,
) -> bytes:
    """content：文档源文本；source_fmt：'markdown' 或 'latex'。返回 .docx 字节。

    cover_title 非空时插入封面页；base_dir 用于解析图片相对路径；
    number_equations 给 $$ 独立公式加 (1)(2) 右对齐编号。
    """
    from docx import Document

    text = _latex_to_markdown(content) if source_fmt in ("latex", "tex") else content

    doc = Document()
    _setup_page(doc)
    if page_numbers:
        _add_page_number_footer(doc)
    if cover_title:
        _add_cover(doc, cover_title, cover_subtitle, cover_meta)
    _render_markdown(doc, text, base_dir, number_equations)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _latex_to_markdown(tex: str) -> str:
    """LaTeX 粗略转 Markdown：保留数学环境，其余剥成文本。"""
    m = re.search(r"\\begin\{document\}(.*?)\\end\{document\}", tex, re.S)
    body = m.group(1) if m else tex
    body = re.sub(r"\\section\*?\{(.*?)\}", r"\n# \1\n", body)
    body = re.sub(r"\\subsection\*?\{(.*?)\}", r"\n## \1\n", body)
    body = re.sub(r"\\subsubsection\*?\{(.*?)\}", r"\n### \1\n", body)
    body = re.sub(r"\\textbf\{(.*?)\}", r"**\1**", body)
    body = re.sub(r"\\(?:textit|emph)\{(.*?)\}", r"*\1*", body)
    # equation / align / \[ \] → $$
    body = re.sub(
        r"\\begin\{(?:equation|align|displaymath)\*?\}(.*?)\\end\{(?:equation|align|displaymath)\*?\}",
        lambda mm: f"\n$$\n{mm.group(1).strip()}\n$$\n", body, flags=re.S,
    )
    body = re.sub(r"\\\[(.*?)\\\]", lambda mm: f"\n$$\n{mm.group(1).strip()}\n$$\n", body, flags=re.S)
    body = re.sub(r"\\\((.*?)\\\)", lambda mm: f"${mm.group(1).strip()}$", body, flags=re.S)
    body = re.sub(r"\\item\s*", "- ", body)
    body = re.sub(r"\\(?:title|author|date)\{(.*?)\}", r"\1", body)
    body = re.sub(r"\\begin\{[^}]+\}|\\end\{[^}]+\}", "", body)
    body = re.sub(r"%.*", "", body)
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip()
