# -*- coding: utf-8 -*-
"""报告生成与双格式导出（官方模板版）

流程：
    数据聚合（period，产量加权平均口径）
      → 报告数据模型（report_model：模板占位符 + 动态表格 + RAG 知识 + 大模型分析文本）
      → ① Word：填充官方模板 101 个占位符 + 插入 6 张动态表格 + 嵌入服务端渲染图表 + 页眉页脚
        ② PDF ：docx→pdf（LibreOffice / Word COM）或 reportlab 原生排版（离线兜底）

对外主入口 `generate(month, product, scene, topic, use_llm)` 返回 Word/PDF 两个下载地址、
所用 PDF 引擎、图表清单、RAG 引用来源与模板占位符残留自检结果。
"""
import os
import re
import time

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from . import charts as charts_mod
from . import pdf_export
from .logging_conf import get_logger
from . import report_model as rm
from .paths import OUTPUT_DIR

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(PROJECT_ROOT, "data", "template_official.docx")
OUT_DIR = OUTPUT_DIR
CHART_DIR = charts_mod.CHART_DIR
os.makedirs(OUT_DIR, exist_ok=True)

log = get_logger(__name__)

PAT = re.compile(r"\{\{(\w+)\}\}")
CAT_KEY = rm.CAT_KEY

# 季度报告的措辞替换（模板表头写的是"上月/去年同月"）
QUARTER_SWAP = [("上月", "上期"), ("去年同月", "去年同期"), ("月度成本分析", "季度成本分析")]


# ============================ 段落/表格填充 ============================
def _fill_paragraph(p, values):
    """填充段落中的 {{占位符}}；未在取值表中的占位符原样保留（它们是动态表格/图表的插入锚点）"""
    full = "".join(r.text for r in p.runs)
    if not PAT.search(full):
        return
    new = PAT.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), full)
    if new == full:
        return
    for r in list(p.runs):
        r.text = ""
    if p.runs:
        p.runs[0].text = new
    else:
        p.add_run(new)


def _fill_tables(doc, values):
    for t in doc.tables:
        for row in t.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    _fill_paragraph(p, values)


def _find_paragraph(doc, anchor_text):
    return next((p for p in doc.paragraphs if anchor_text in p.text), None)


def _style_table(tbl, header_rows=1):
    """统一表格样式：Table Grid + 表头加粗底色 + 居中"""
    try:
        tbl.style = "Table Grid"
    except Exception:
        pass
    tbl.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, row in enumerate(tbl.rows):
        for cell in row.cells:
            for p in cell.paragraphs:
                if i < header_rows:
                    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    for r in p.runs:
                        r.font.bold = True
                        r.font.size = Pt(9)
                else:
                    for r in p.runs:
                        r.font.size = Pt(9)


def _insert_table(doc, anchor_text, header, rows):
    """在占位符段落位置插入动态表格，返回表格 XML 元素（便于随后插图）"""
    target = _find_paragraph(doc, anchor_text)
    if target is None:
        return None
    tbl = doc.add_table(rows=1, cols=len(header))
    for j, h in enumerate(header):
        tbl.rows[0].cells[j].text = str(h)
    for row in rows:
        cells = tbl.add_row().cells
        for j, val in enumerate(row):
            cells[j].text = str(val)
    _style_table(tbl)
    target._p.addnext(tbl._tbl)
    for r in target.runs:
        r.text = ""
    return tbl._tbl


def _png_ok(path, min_bytes=1000):
    """校验图表文件可用（存在、体积正常、PNG 魔数正确）"""
    try:
        if os.path.getsize(path) < min_bytes:
            return False
        with open(path, "rb") as f:
            return f.read(8) == b"\x89PNG\r\n\x1a\n"
    except OSError:
        return False


def _pic_elements(doc, png, caption=None, width_cm=15.5):
    """生成"图片段落 + 图注段落"的 XML 元素列表（此时还挂在文末）

    图表文件刚写入时可能仍被杀毒/索引进程短暂占用，这里做"校验 + 重试"，
    仍不可用则跳过该图并告警——**单张图失败不允许影响整份报告生成**。
    """
    if not _png_ok(png):
        for wait in (0.2, 0.5, 1.0):
            time.sleep(wait)
            if _png_ok(png):
                break
        else:
            log.warning("图表文件不可用，已跳过嵌入：%s", png)
            return []
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    try:
        p.add_run().add_picture(png, width=Cm(width_cm))
    except Exception as e:
        log.warning("图表嵌入失败（已跳过）%s：%s", os.path.basename(png), e)
        p._element.getparent().remove(p._element)
        return []
    els = [p._p]
    if caption:
        cp = doc.add_paragraph()
        cp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = cp.add_run(caption)
        r.font.size = Pt(9)
        r.font.color.rgb = RGBColor(0x5A, 0x6B, 0x7B)
        els.append(cp._p)
    return els


def _insert_after(doc, anchor, png, caption=None, width_cm=15.5):
    """把图片（含图注）插入到指定元素之后；anchor 可为 Paragraph 或 XML 元素"""
    try:
        return _insert_after_raw(doc, anchor, png, caption, width_cm)
    except Exception as e:      # 图表问题绝不能中断报告生成
        log.warning("插图失败（已跳过）%s：%s", os.path.basename(str(png)), e)
        return None


def _insert_after_raw(doc, anchor, png, caption=None, width_cm=15.5):
    if anchor is None or not png or not os.path.exists(png):
        return None
    anchor_el = getattr(anchor, "_p", None)
    if anchor_el is None:
        anchor_el = getattr(anchor, "_tbl", None)
    if anchor_el is None:
        anchor_el = anchor
    if not hasattr(anchor_el, "addnext"):
        return None
    els = _pic_elements(doc, png, caption, width_cm)
    for el in reversed(els):          # 逆序插入，保证图片在图注之前
        anchor_el.addnext(el)
    return els[-1]


def _append_block(doc, title, lines, style="Normal"):
    """在文末追加一个小节（标题 + 正文），返回最后插入的元素"""
    h = doc.add_paragraph()
    r = h.add_run(title)
    r.font.bold = True
    r.font.size = Pt(11)
    r.font.color.rgb = RGBColor(0x1F, 0x4E, 0x79)
    last = h._p
    for line in lines:
        p = doc.add_paragraph()
        run = p.add_run(line)
        run.font.size = Pt(9.5)
        last = p._p
    return last


def _add_sources_appendix(doc, model, anchor_paragraph_text=None):
    """附录：RAG 检索命中的知识库来源（体现"知识增强"证据链）"""
    src_tbl = model["tables"]["sources"]
    head = doc.add_paragraph()
    r = head.add_run("附录 · 知识库引用来源（RAG 检索命中）")
    r.font.bold = True
    r.font.size = Pt(11.5)
    r.font.color.rgb = RGBColor(0x1F, 0x4E, 0x79)
    note = doc.add_paragraph()
    nr = note.add_run(f"本期共命中 {len(model['sources'])} 条知识库片段，由 jieba 分词 + BM25（可选向量）混合检索得到，"
                      f"已作为大模型分析的知识依据；检索来源包含行业行情、生产工艺、GMP 法规与产品配方文档。")
    nr.font.size = Pt(9.5)
    tbl = doc.add_table(rows=1, cols=len(src_tbl["表头"]))
    for j, h in enumerate(src_tbl["表头"]):
        tbl.rows[0].cells[j].text = str(h)
    for row in src_tbl["行"]:
        cells = tbl.add_row().cells
        for j, val in enumerate(row):
            cells[j].text = str(val)
    _style_table(tbl)
    return tbl._tbl


def _retitle_quarter(doc):
    """季度报告措辞替换（上月→上期、去年同月→去年同期）"""
    def fix(p):
        for run in p.runs:
            for a, b in QUARTER_SWAP:
                if a in run.text:
                    run.text = run.text.replace(a, b)

    for p in doc.paragraphs:
        fix(p)
    for t in doc.tables:
        for row in t.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    fix(p)


# ============================ 页眉页脚 ============================
def _add_field(paragraph, instr):
    """插入域代码（PAGE / NUMPAGES），Word 打开后自动显示页码"""
    run = paragraph.add_run()
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    instr_el = OxmlElement("w:instrText")
    instr_el.set(qn("xml:space"), "preserve")
    instr_el.text = f" {instr} "
    fld_sep = OxmlElement("w:fldChar")
    fld_sep.set(qn("w:fldCharType"), "separate")
    cached = OxmlElement("w:t")          # 缓存值：Word 打开/打印时自动刷新为真实页码
    cached.text = "1"
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    run._r.append(fld_begin)
    run._r.append(instr_el)
    run._r.append(fld_sep)
    run._r.append(cached)
    run._r.append(fld_end)
    return run


def _clear_part(part):
    """清空页眉/页脚已有内容（保留段落对象）"""
    for p in list(part.paragraphs[1:]):
        p._element.getparent().remove(p._element)
    p0 = part.paragraphs[0]
    for r in list(p0.runs):
        r._element.getparent().remove(r._element)
    return p0


def _set_header_footer(doc, model):
    """设置专业页眉页脚：页眉=报告标题+报告类型/期间；页脚=编制单位 + 第X页/共Y页 + 保密标识"""
    from docx.shared import Emu
    for idx, s in enumerate(doc.sections):
        page_w = s.page_width or Emu(7560310)
        left = s.left_margin or Emu(1141095)
        right = s.right_margin or left
        usable = page_w - left - right
        if idx == 0:                     # 封面页不显示页眉页脚
            try:
                _clear_part(s.header)
                _clear_part(s.footer)
            except Exception:
                pass
            continue

        hp = _clear_part(s.header)
        hp.paragraph_format.tab_stops.add_tab_stop(usable, WD_TAB_ALIGNMENT.RIGHT)
        r1 = hp.add_run(model["标题"])
        r1.font.size = Pt(9)
        r1.font.bold = True
        r1.font.color.rgb = RGBColor(0x1F, 0x4E, 0x79)
        r2 = hp.add_run("\t" + f"{model['报告类型']} · {model['期间']}")
        r2.font.size = Pt(8.5)
        r2.font.color.rgb = RGBColor(0x5A, 0x6B, 0x7B)
        _add_bottom_border(hp)

        fp = _clear_part(s.footer)
        fp.paragraph_format.tab_stops.add_tab_stop(Emu(int(usable / 2)), WD_TAB_ALIGNMENT.CENTER)
        fp.paragraph_format.tab_stops.add_tab_stop(usable, WD_TAB_ALIGNMENT.RIGHT)
        rf1 = fp.add_run(f"{model['编制单位']} · {model['编制日期']}")
        rf1.font.size = Pt(8)
        rf1.font.color.rgb = RGBColor(0x6B, 0x77, 0x85)
        rf2 = fp.add_run("\t第 ")
        rf2.font.size = Pt(8.5)
        rf2.font.color.rgb = RGBColor(0x1F, 0x4E, 0x79)
        f_page = _add_field(fp, "PAGE")
        f_page.font.size = Pt(8.5)
        f_page.font.bold = True
        f_page.font.color.rgb = RGBColor(0x1F, 0x4E, 0x79)
        rf3 = fp.add_run(" 页 / 共 ")
        rf3.font.size = Pt(8.5)
        rf3.font.color.rgb = RGBColor(0x1F, 0x4E, 0x79)
        f_total = _add_field(fp, "NUMPAGES")
        f_total.font.size = Pt(8.5)
        f_total.font.bold = True
        f_total.font.color.rgb = RGBColor(0x1F, 0x4E, 0x79)
        rf4 = fp.add_run(" 页\t内部资料 · 注意保密")
        rf4.font.size = Pt(8)
        rf4.font.color.rgb = RGBColor(0x6B, 0x77, 0x85)


def _add_bottom_border(paragraph):
    p_pr = paragraph._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "1F4E79")
    borders.append(bottom)
    p_pr.append(borders)


def _leftovers(doc):
    """扫描残留的 `{{占位符}}`：**段落与表格单元格都要扫**（模板里多数占位符在表格内）"""
    out = [p.text.strip() for p in doc.paragraphs if "{{" in p.text]
    for t in doc.tables:
        for row in t.rows:
            for cell in row.cells:
                if "{{" in cell.text:
                    out.append(cell.text.strip())
    return out


# ============================ Word 渲染 ============================
def render_docx(model, out_path):
    """按官方模板渲染 Word 报告（占位符 + 动态表格 + 图表 + 页眉页脚 + 附录）"""
    if not os.path.exists(TPL):
        raise FileNotFoundError(f"未找到官方报告模板：{TPL}")
    v = model["values"]
    doc = Document(TPL)

    # 先记录会被"文本取值"替换掉的占位符段落（填充后其原文即消失），作为插图锚点
    pre_anchors = {key: _find_paragraph(doc, key) for key in ("{{成本异常排查分析}}",)}

    for p in doc.paragraphs:
        _fill_paragraph(p, v)
    _fill_tables(doc, v)

    t = model["tables"]
    ch = model["charts"]
    # ① 核心指标（2.1 后嵌入 实际 vs 上期/去年同期/预算 对比图）
    _insert_after(doc, _find_paragraph(doc, "2.1 核心指标一览"),
                  ch.get("budget_compare"), charts_mod.CAPTIONS.get("budget_compare"), 11.5)
    # ② 成本结构图（2.2 成本结构后）
    _insert_after(doc, _find_paragraph(doc, "2.2 成本结构"),
                  ch.get("structure"), charts_mod.CAPTIONS.get("structure"), 11.5)
    # ③ 近6个月成本趋势
    el = _insert_table(doc, "{{近6个月成本趋势表格}}", t["trend"]["表头"], t["trend"]["行"])
    el = _insert_after(doc, el, ch.get("trend"), charts_mod.CAPTIONS.get("trend"))
    # ④ 原材料成本明细 + 影响图
    el = _insert_table(doc, "{{原材料成本明细表格}}", t["material"]["表头"], t["material"]["行"])
    el = _insert_after(doc, el, ch.get("material_impact"), charts_mod.CAPTIONS.get("material_impact"))
    # ⑤ 原材料价格跟踪 + 行情图
    el = _insert_table(doc, "{{原材料价格跟踪表格}}", t["price"]["表头"], t["price"]["行"])
    el = _insert_after(doc, el, ch.get("price_trend"), charts_mod.CAPTIONS.get("price_trend"))
    # ⑥ 成本异常排查 + 瀑布图
    el = _insert_after(doc, pre_anchors.get("{{成本异常排查分析}}"),
                       ch.get("waterfall"), charts_mod.CAPTIONS.get("waterfall"))
    # ⑦ 对标差异表 + 两厂对比图
    el = _insert_table(doc, "{{对标差异表格}}", t["benchmark"]["表头"], t["benchmark"]["行"])
    el = _insert_after(doc, el, ch.get("benchmark"), charts_mod.CAPTIONS.get("benchmark"))
    # ⑧ 改进建议 / 整改任务
    _insert_table(doc, "{{改进建议表格}}", t["suggest"]["表头"], t["suggest"]["行"])
    _insert_table(doc, "{{整改任务表格}}", t["tasks"]["表头"], t["tasks"]["行"])

    # 专题聚焦（插在"三、成本要素明细分析"之前，作为第二章的小节）
    if model["专题"]:
        anchor = _find_paragraph(doc, "三、成本要素明细分析")
        if anchor is not None:
            head = anchor.insert_paragraph_before(f"2.3 专题聚焦 · {model['专题']}")
            hr = head.runs[0] if head.runs else head.add_run("")
            hr.font.bold = True
            hr.font.size = Pt(10.5)
            hr.font.color.rgb = RGBColor(0x1F, 0x4E, 0x79)
            lines = [x for x in model["topic_text"].split("\n") if x.strip()]
            for i, line in enumerate(lines):
                p = anchor.insert_paragraph_before(line)
                run = p.runs[0] if p.runs else p.add_run(line)
                run.font.size = Pt(9.5)

    # 附录：知识库引用来源
    _add_sources_appendix(doc, model)

    # 页眉页脚 / 季度措辞
    _set_header_footer(doc, model)
    if model["period"]["kind"] == "季度":
        _retitle_quarter(doc)

    doc.save(out_path)
    _check = Document(out_path)
    leftover = _leftovers(_check)
    n_tables = len(_check.tables)
    if leftover:
        log.warning("报告存在 %d 处未替换的占位符：%s", len(leftover), leftover[:3])
    return {"leftover": leftover, "tables": n_tables}


# ============================ 主入口 ============================
def generate(month, product=None, scene="月度成本分析", topic=None, use_llm=True,
             want_charts=True, pdf=True):
    """生成报告：Word + PDF 双格式导出"""
    product = product or rm.ds.products_list()[0]
    model = rm.build(month, product, scene=scene, topic=topic, use_llm=use_llm,
                     want_charts=want_charts)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    per = model["period"]
    if per["kind"] == "季度":
        period_tag = f"{per['year']}Q{per['quarter']}"
    else:
        period_tag = per["label"]
    extra = f"_{model['专题']}" if model["专题"] else ""
    base = f"report_{period_tag}_{product}{extra}_{stamp}"
    base = re.sub(r'[\\/:*?"<>|\s]', "", base)
    docx_name = f"{base}.docx"
    pdf_name = f"{base}.pdf"
    docx_path = os.path.join(OUT_DIR, docx_name)
    pdf_path = os.path.join(OUT_DIR, pdf_name)

    info = render_docx(model, docx_path)

    pdf_res = {"ok": False, "engine": None, "note": "已跳过 PDF 导出"}
    if pdf:
        try:
            pdf_res = pdf_export.export_pdf(docx_path, pdf_path, model=model)
        except Exception as e:
            pdf_res = {"ok": False, "engine": None, "note": f"PDF 导出异常：{e}"}

    charts_url = {k: f"/api/report/chart?file={os.path.basename(p)}" for k, p in model["charts"].items()}
    return {
        "file": docx_name, "download": f"/api/report/download?file={docx_name}",
        "file_pdf": pdf_name if pdf_res.get("ok") else None,
        "download_pdf": f"/api/report/download?file={pdf_name}" if pdf_res.get("ok") else None,
        "pdf_engine": pdf_res.get("engine"), "pdf_note": pdf_res.get("note"),
        "pdf_ok": bool(pdf_res.get("ok")), "pdf_tried": pdf_res.get("tried", []),
        "charts": charts_url,
        "chart_captions": {k: charts_mod.CAPTIONS.get(k, "") for k in model["charts"]},
        "scene": scene, "topic": model["专题"], "product": product,
        "period": {"kind": model["period"]["kind"], "label": model["period"]["label"],
                   "span": model["period"]["span"], "months": model["period"]["months"]},
        "values": {k2: val for k2, val in model["values"].items() if isinstance(val, str) and len(val) < 3000},
        "alerts": model["alerts"], "sources": model["sources"], "engine": model["engine"],
        "leftover": info["leftover"],
        "stats": {"表格数": info["tables"], "图表数": len(model["charts"]),
                  "图表引擎": charts_mod.engine() or "无（未安装 matplotlib / Pillow）",
                  "知识库引用": len(model["sources"]),
                  "报告标题": model["标题"]},
    }
