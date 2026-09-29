# -*- coding: utf-8 -*-
"""PDF 导出：三级可插拔后端，保证"任何环境都能出 PDF"

默认顺序（`PDF_ENGINE=auto`）与特点：

1. **libreoffice**：`soffice --headless --convert-to pdf`，与 Word 排版 100% 一致，
   页眉页脚/表格边框/嵌入图片全部保留（跨平台；服务器/CI 推荐安装）。
2. **native**：纯 Python（reportlab）原生渲染 PDF，**约 1 秒出稿、不依赖 Office / LibreOffice**。
   直接复用报告数据模型（章节 + 表格 + 图表 PNG + RAG 引用来源），自行绘制
   页眉（报告标题 / 报告类型·期间）、页脚（编制单位 + 「第 X 页 / 共 Y 页」）、
   专业表格样式与图表嵌入 —— 演示与离线环境的稳定首选。
3. **word**：调用本机 Microsoft Word 导出 PDF，与 Word 所见即所得（Windows + Office 环境）。
   优先用 `pywin32`；未安装时自动改用系统自带 PowerShell 调 COM，无需额外依赖。

`export_pdf()` 依次尝试并对产物做体检（%PDF 头 / 体积 / 页数），返回实际使用的引擎与说明，
前端会展示 `pdf_engine`。也可用 `PDF_ENGINE=libreoffice|native|word` 强制指定单一后端。
"""
import os
import shutil
import subprocess
import sys
from .logging_conf import get_logger

log = get_logger(__name__)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ============================ 公共 ============================
def _abs(p):
    return os.path.abspath(p)


def available_backends():
    """当前环境可用的 PDF 后端列表"""
    out = []
    if find_soffice():
        out.append("libreoffice")
    if word_available():
        out.append("word")
    if native_available():
        out.append("native")
    return out


# ==================== 后端 1：LibreOffice ====================
_SOFFICE_CANDIDATES = [
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    "/usr/bin/soffice", "/usr/bin/libreoffice", "/usr/local/bin/soffice",
    "/snap/bin/libreoffice",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
]


def find_soffice():
    env = os.environ.get("SOFFICE_BIN")
    if env and os.path.exists(env):
        return env
    for name in ("soffice", "soffice.exe", "libreoffice"):
        p = shutil.which(name)
        if p:
            return p
    for c in _SOFFICE_CANDIDATES:
        if os.path.exists(c):
            return c
    return None


def convert_with_libreoffice(docx_path, pdf_path, timeout=240):
    soffice = find_soffice()
    if not soffice:
        return False, "未检测到 LibreOffice（soffice）"
    outdir = os.path.dirname(_abs(pdf_path)) or "."
    os.makedirs(outdir, exist_ok=True)
    cmd = [soffice, "--headless", "--norestore", "--convert-to", "pdf:writer_pdf_Export",
           "--outdir", outdir, _abs(docx_path)]
    try:
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=flags)
    except Exception as e:
        return False, f"LibreOffice 转换异常：{e}"
    produced = os.path.join(outdir, os.path.splitext(os.path.basename(docx_path))[0] + ".pdf")
    if not os.path.exists(produced):
        msg = (r.stderr or r.stdout or b"").decode("utf-8", "ignore")[:300]
        return False, f"LibreOffice 未生成 PDF：{msg}"
    if _abs(produced) != _abs(pdf_path):
        shutil.move(produced, pdf_path)
    return True, "已用 LibreOffice 转换（版式与 Word 一致）"


# ==================== 后端 2：Microsoft Word (COM) ====================
def _win_word_installed():
    """注册表探测本机是否安装 Microsoft Word（不依赖 pywin32）"""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                winreg.OpenKey(root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\winword.exe")
                return True
            except OSError:
                continue
    except Exception:
        pass
    return False


def word_available():
    """本机是否可走 Word 导出：pywin32 或 PowerShell COM 任一可用即可"""
    if sys.platform != "win32" or not _win_word_installed():
        return False
    if shutil.which("powershell") or shutil.which("pwsh"):
        return True
    try:
        import win32com.client  # noqa: F401
        return True
    except Exception:
        return False


_PS_SCRIPT = """# auto-generated: export a .docx to PDF via Microsoft Word COM
$ErrorActionPreference = 'Stop'
$src = '{src}'
$dst = '{dst}'
$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = 0
try {{ $word.AutomationSecurity = 3 }} catch {{ }}
$doc = $null
try {{
  $doc = $word.Documents.Open($src, $false, $true, $false)
  try {{ $doc.ExportAsFixedFormat($dst, 17) }}
  catch {{ $doc.SaveAs([ref]$dst, [ref]17) }}
  Write-Output 'OK'
}} finally {{
  if ($doc -ne $null) {{
    try {{ $doc.Close(0) }} catch {{ }}
    try {{ [System.Runtime.InteropServices.Marshal]::ReleaseComObject($doc) | Out-Null }} catch {{ }}
  }}
  try {{ $word.Quit(0) }} catch {{ }}
  try {{ [System.Runtime.InteropServices.Marshal]::ReleaseComObject($word) | Out-Null }} catch {{ }}
  [GC]::Collect(); [GC]::WaitForPendingFinalizers()
}}
"""


def _win_word_pids():
    """当前 WINWORD.EXE 进程号集合（用于超时后只清理本次调用拉起的 Word 实例）"""
    if sys.platform != "win32":
        return set()
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq WINWORD.EXE", "/FO", "CSV", "/NH"],
                             capture_output=True, timeout=20,
                             creationflags=subprocess.CREATE_NO_WINDOW).stdout or b""
        pids = set()
        for line in out.decode("utf-8", "ignore").splitlines():
            parts = [p.strip('"') for p in line.split('","')]
            if len(parts) >= 2 and parts[1].isdigit():
                pids.add(int(parts[1]))
        return pids
    except Exception:
        return set()


def _kill_new_word(before):
    """只结束本次新增的 Word 进程，避免影响用户已打开的 Word"""
    for pid in (_win_word_pids() - set(before)):
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=20,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception:
            pass


def _ps_literal(path):
    return _abs(path).replace("'", "''")


def convert_with_word_powershell(docx_path, pdf_path, timeout=90, attempts=2):
    """不依赖 pywin32：通过 PowerShell 调用本机 Word COM 导出 PDF（Windows）

    Word COM 偶发长时间无响应，故默认尝试 2 次（每次超时后清理本次拉起的 Word 进程）。
    """
    shell = shutil.which("powershell") or shutil.which("pwsh")
    if not shell:
        return False, "未找到 powershell，无法调用 Word"
    script_path = os.path.join(os.environ.get("TEMP", "."), "dsh_word2pdf.ps1")
    with open(script_path, "w", encoding="utf-8-sig") as f:
        f.write(_PS_SCRIPT.format(src=_ps_literal(docx_path), dst=_ps_literal(pdf_path)))
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    last = "未执行"
    for attempt in range(1, attempts + 1):
        if os.path.exists(pdf_path):
            try:
                os.remove(pdf_path)
            except OSError:
                pass
        before = _win_word_pids()
        try:
            r = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                "-File", script_path],
                               capture_output=True, timeout=timeout, creationflags=flags)
        except subprocess.TimeoutExpired:
            _kill_new_word(before)   # 清理本次拉起的 Word，避免实例残留导致后续转换卡死
            last = f"Word 转换超时（>{timeout}s，第 {attempt} 次），已清理残留 Word 进程"
            continue
        except Exception as e:
            return False, f"PowerShell 调用 Word 失败：{e}"
        if os.path.exists(pdf_path) and _is_pdf(pdf_path):
            return True, "已用 Microsoft Word 导出（PowerShell COM，与 Word 版式一致）"
        last = _decode(r.stderr or r.stdout or b"")[:300] or "Word 未生成 PDF"
    return False, last


def _is_pdf(path):
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"%PDF"
    except Exception:
        return False


def convert_with_word(docx_path, pdf_path):
    if not word_available():
        return False, "本机未安装 Microsoft Word"
    try:
        import pythoncom
        import win32com.client
    except Exception:
        return convert_with_word_powershell(docx_path, pdf_path)

    pythoncom.CoInitialize()
    app = None
    try:
        app = win32com.client.DispatchEx("Word.Application")
        app.Visible = False
        app.DisplayAlerts = 0
        doc = app.Documents.Open(_abs(docx_path), ReadOnly=True, AddToRecentFiles=False)
        try:
            # wdExportFormatPDF = 17
            doc.ExportAsFixedFormat(OutputFileName=_abs(pdf_path), ExportFormat=17,
                                    OpenAfterExport=False, OptimizeFor=0, CreateBookmarks=1,
                                    DocStructureTags=True, BitmapMissingFonts=True)
        except Exception:
            doc.SaveAs2(_abs(pdf_path), FileFormat=17)   # wdFormatPDF
        doc.Close(False)
        ok = _is_pdf(pdf_path)
        return ok, "已用 Microsoft Word 导出（与 Word 版式完全一致）" if ok else "Word 未生成 PDF"
    except Exception as e:
        return False, f"Word 导出异常：{e}"
    finally:
        try:
            if app is not None:
                app.Quit()
        except Exception:
            pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


# ==================== 后端 3：原生 reportlab ====================
def native_available():
    try:
        import reportlab  # noqa: F401
        return True
    except Exception:
        return False


FONT, FONT_BOLD = "STSong-Light", "STSong-Light"
_FONT_DONE = False

_TTF_CANDIDATES = [
    ("RptSong", "RptSongB", r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc"),
    ("RptSong", "RptSongB", r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\simhei.ttf"),
    ("RptSong", "RptSongB", r"C:\Windows\Fonts\simsun.ttc", r"C:\Windows\Fonts\simsunb.ttf"),
    ("RptSong", "RptSongB", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
     "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
    ("RptSong", "RptSongB", "/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/PingFang.ttc"),
]


def _register_fonts():
    """注册中文字体：优先系统 TTF，缺失时退化为 reportlab 内置中文 CID 字体"""
    global FONT, FONT_BOLD, _FONT_DONE
    if _FONT_DONE:
        return
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    for reg, bold, reg_path, bold_path in _TTF_CANDIDATES:
        if not os.path.exists(reg_path):
            continue
        try:
            pdfmetrics.registerFont(TTFont(reg, reg_path, subfontIndex=0)
                                    if reg_path.lower().endswith(".ttc") else TTFont(reg, reg_path))
        except Exception:
            continue
        FONT = reg
        FONT_BOLD = reg
        try:
            if os.path.exists(bold_path):
                pdfmetrics.registerFont(TTFont(bold, bold_path, subfontIndex=0)
                                        if bold_path.lower().endswith(".ttc") else TTFont(bold, bold_path))
                FONT_BOLD = bold
        except Exception:
            pass
        if FONT == reg:
            _FONT_DONE = True
            return
    try:
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        FONT = FONT_BOLD = "STSong-Light"
    except Exception:
        FONT = FONT_BOLD = "Helvetica"
    _FONT_DONE = True


def _styles():
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
    from reportlab.lib.styles import ParagraphStyle
    return {
        "cover_title": ParagraphStyle("cover_title", fontName=FONT_BOLD, fontSize=24, leading=34,
                                      alignment=TA_CENTER, textColor="#12365B", wordWrap="CJK"),
        "cover_sub": ParagraphStyle("cover_sub", fontName=FONT, fontSize=13, leading=22,
                                    alignment=TA_CENTER, textColor="#3B6EA5", wordWrap="CJK"),
        "cover_meta": ParagraphStyle("cover_meta", fontName=FONT, fontSize=10.5, leading=20,
                                     alignment=TA_CENTER, textColor="#555555", wordWrap="CJK"),
        "h1": ParagraphStyle("h1", fontName=FONT_BOLD, fontSize=14.5, leading=22, spaceBefore=14,
                             spaceAfter=7, textColor="#12365B", wordWrap="CJK"),
        "h2": ParagraphStyle("h2", fontName=FONT_BOLD, fontSize=11.5, leading=18, spaceBefore=10,
                             spaceAfter=5, textColor="#1F4E79", wordWrap="CJK"),
        "body": ParagraphStyle("body", fontName=FONT, fontSize=10, leading=16.5, spaceAfter=5,
                               alignment=TA_JUSTIFY, textColor="#222222", wordWrap="CJK",
                               firstLineIndent=0),
        "note": ParagraphStyle("note", fontName=FONT, fontSize=8.6, leading=13.5, spaceAfter=3,
                               textColor="#6B7785", wordWrap="CJK"),
        "caption": ParagraphStyle("caption", fontName=FONT, fontSize=8.8, leading=13,
                                  alignment=TA_CENTER, textColor="#5A6B7B", spaceBefore=2,
                                  spaceAfter=8, wordWrap="CJK"),
        "cell": ParagraphStyle("cell", fontName=FONT, fontSize=8.4, leading=12, textColor="#222222",
                               wordWrap="CJK"),
        "cell_r": ParagraphStyle("cell_r", fontName=FONT, fontSize=8.4, leading=12, alignment=2,
                                 textColor="#222222", wordWrap="CJK"),
        "cell_h": ParagraphStyle("cell_h", fontName=FONT_BOLD, fontSize=8.6, leading=12,
                                 alignment=TA_CENTER, textColor="#FFFFFF", wordWrap="CJK"),
    }


def _make_canvas_class(meta):
    """生成两遍绘制画布类：先收集页数，再统一补画「第 X 页 / 共 Y 页」"""
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.lib import colors as rl_colors
    from reportlab.lib.units import cm

    class NumberedCanvas(rl_canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved = []

        def showPage(self):
            self._saved.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved)
            for i, state in enumerate(self._saved, start=1):
                self.__dict__.update(state)
                self._decorate(i, total)
                super().showPage()
            super().save()

        def _decorate(self, page_no, total):
            if page_no <= 1:      # 封面不加页眉页脚
                return
            w, h = self._pagesize
            self.saveState()
            self.setFont(FONT_BOLD, 9)
            self.setFillColor(rl_colors.HexColor("#1F4E79"))
            self.drawString(2.0 * cm, h - 1.42 * cm, meta.get("页眉左", ""))
            self.setFont(FONT, 8.5)
            self.setFillColor(rl_colors.HexColor("#5A6B7B"))
            self.drawRightString(w - 2.0 * cm, h - 1.42 * cm, meta.get("页眉右", ""))
            self.setStrokeColor(rl_colors.HexColor("#1F4E79"))
            self.setLineWidth(0.8)
            self.line(2.0 * cm, h - 1.55 * cm, w - 2.0 * cm, h - 1.55 * cm)

            self.setStrokeColor(rl_colors.HexColor("#C9D3DE"))
            self.setLineWidth(0.5)
            self.line(2.0 * cm, 1.72 * cm, w - 2.0 * cm, 1.72 * cm)
            self.setFont(FONT, 8.2)
            self.setFillColor(rl_colors.HexColor("#6B7785"))
            self.drawString(2.0 * cm, 1.35 * cm, meta.get("页脚左", ""))
            self.setFont(FONT_BOLD, 8.4)
            self.setFillColor(rl_colors.HexColor("#1F4E79"))
            self.drawCentredString(w / 2.0, 1.35 * cm, f"第 {page_no} 页 / 共 {total} 页")
            self.setFont(FONT, 8.2)
            self.setFillColor(rl_colors.HexColor("#6B7785"))
            self.drawRightString(w - 2.0 * cm, 1.35 * cm, meta.get("页脚右", ""))
            self.restoreState()

    return NumberedCanvas


def _fmt_cell(v):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:,.2f}"
    return str(v)


def _is_number(s):
    t = str(s).replace(",", "").replace("%", "").replace("+", "").replace("—", "").strip()
    if t in ("", "-"):
        return False
    try:
        float(t)
        return True
    except ValueError:
        return False


def _build_table(header, rows, st, total_width):
    """生成专业排版的 reportlab 表格（自动列宽 + 数字右对齐 + 首行重复）"""
    from reportlab.lib import colors as rl_colors
    from reportlab.platypus import Paragraph, Table, TableStyle

    header = [str(h) for h in header]
    body_rows = [[_fmt_cell(c) for c in r] for r in rows]

    # 数字列右对齐
    align_right = []
    for j in range(len(header)):
        vals = [r[j] for r in body_rows if j < len(r)]
        nums = [v for v in vals if _is_number(v)]
        align_right.append(bool(vals) and len(nums) >= max(1, int(0.6 * len(vals))))

    # 列宽：按内容显示宽度加权（中文按 2 个字符宽计）
    def disp_len(s):
        s = str(s)
        return sum(2 if ord(ch) > 0x2000 else 1 for ch in s)

    weights = []
    for j, h in enumerate(header):
        m = disp_len(h)
        for r in body_rows[:60]:
            if j < len(r):
                m = max(m, disp_len(r[j]))
        weights.append(min(max(m, 6), 30) ** 1.05)
    total_w = sum(weights)
    widths = [max(total_width * w / total_w, 1.5 * 28) for w in weights]
    scale = total_width / sum(widths)
    widths = [w * scale for w in widths]

    data = [[Paragraph(h, st["cell_h"]) for h in header]]
    for r in body_rows:
        cells = []
        for j, v in enumerate(r):
            style = st["cell_r"] if align_right[j] else st["cell"]
            cells.append(Paragraph(str(v).replace("&", "&amp;").replace("<", "&lt;"), style))
        data.append(cells)

    tbl = Table(data, colWidths=widths, repeatRows=1, hAlign="CENTER")
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), rl_colors.HexColor("#1F4E79")),
        ("TEXTCOLOR", (0, 0), (-1, 0), rl_colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, rl_colors.HexColor("#B7C4D4")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3.6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.6),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [rl_colors.white, rl_colors.HexColor("#F2F6FA")]),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, rl_colors.HexColor("#12365B")),
    ]
    tbl.setStyle(TableStyle(style))
    return tbl


def render_native(model, pdf_path, meta=None):
    """用 reportlab 原生渲染 PDF（不依赖 Office）"""
    if not native_available():
        return False, "未安装 reportlab"
    try:
        _register_fonts()
        from reportlab.lib import colors as rl_colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import cm
        from reportlab.lib.utils import ImageReader
        from reportlab.platypus import (BaseDocTemplate, Frame, Image, KeepTogether, PageBreak,
                                        PageTemplate, Paragraph, Spacer, Table, TableStyle)
    except Exception as e:
        return False, f"reportlab 初始化失败：{e}"

    st = _styles()
    page_w, page_h = A4
    margin_lr, margin_top, margin_bottom = 2.0 * cm, 2.3 * cm, 2.05 * cm
    content_w = page_w - 2 * margin_lr

    meta = dict(meta or {})
    meta.setdefault("页眉左", model.get("标题", "成本分析报告"))
    meta.setdefault("页眉右", f"{model.get('报告类型', '')} · {model.get('期间', '')}")
    meta.setdefault("页脚左", model.get("编制单位", ""))
    meta.setdefault("页脚右", "内部资料 · 注意保密")
    canvas_cls = _make_canvas_class(meta)

    doc = BaseDocTemplate(_abs(pdf_path), pagesize=A4, title=model.get("标题", "成本分析报告"),
                          author=model.get("编制单位", ""), subject=model.get("报告类型", ""),
                          leftMargin=margin_lr, rightMargin=margin_lr,
                          topMargin=margin_top, bottomMargin=margin_bottom)
    frame = Frame(margin_lr, margin_bottom, content_w, page_h - margin_top - margin_bottom, id="main")
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame])])

    flow = []
    # ---------- 封面 ----------
    flow.append(Spacer(1, 3.2 * cm))
    flow.append(Paragraph(model.get("标题", "成本分析报告"), st["cover_title"]))
    flow.append(Spacer(1, 0.5 * cm))
    flow.append(Paragraph(model.get("副标题", ""), st["cover_sub"]))
    flow.append(Spacer(1, 1.6 * cm))
    cover_rows = model.get("封面信息") or []
    info = [[Paragraph(f"<b>{k}</b>", st["cell_h"]), Paragraph(str(v), st["cell"])] for k, v in cover_rows]
    info_tbl = Table(info, colWidths=[content_w * 0.3, content_w * 0.7], hAlign="CENTER")
    info_tbl.setStyle(TableStyle([
        # 注意：ROWBACKGROUNDS 放在列背景之前会被覆盖，故只作用于第 2 列
        ("ROWBACKGROUNDS", (1, 0), (1, -1), [rl_colors.white, rl_colors.HexColor("#F5F8FC")]),
        ("BACKGROUND", (0, 0), (0, -1), rl_colors.HexColor("#1F4E79")),
        ("GRID", (0, 0), (-1, -1), 0.4, rl_colors.HexColor("#B7C4D4")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    flow.append(info_tbl)
    flow.append(Spacer(1, 1.2 * cm))
    for line in model.get("封面说明", []) or []:
        flow.append(Paragraph(line, st["cover_meta"]))
    flow.append(PageBreak())

    # ---------- 正文 ----------
    for sec in model.get("章节", []):
        flow.append(Paragraph(sec.get("标题", ""), st["h1"]))
        for item in sec.get("内容", []):
            t = item.get("类型")
            if t == "段落":
                for para in str(item.get("文本", "")).split("\n"):
                    if para.strip():
                        txt = para.strip().replace("&", "&amp;").replace("<", "&lt;")
                        flow.append(Paragraph(txt, st[item.get("样式", "body")]))
            elif t == "要点":
                for line in item.get("列表", []):
                    flow.append(Paragraph(f"• {str(line)}", st["body"]))
            elif t == "表格":
                head = item.get("表头") or []
                rows = item.get("行") or []
                cap = item.get("标题")
                block = []
                if cap:
                    block.append(Paragraph(cap, st["note"]))
                block.append(_build_table(head, rows, st, content_w))
                block.append(Spacer(1, 0.35 * cm))
                if len(rows) <= 12:
                    flow.append(KeepTogether(block))
                else:
                    flow.extend(block)
            elif t == "图":
                path = item.get("路径")
                if not path or not os.path.exists(path):
                    continue
                try:
                    # 以内存流方式读图：避免 reportlab 按文件名缓存 ImageReader 而长期占用文件句柄
                    # （Windows 下句柄未释放会导致下一轮"清理图表 → 重写同名文件"失败）
                    import io as _io
                    with open(path, "rb") as f:
                        data = f.read()
                    buf = _io.BytesIO(data)
                    iw, ih = ImageReader(buf).getSize()
                    buf.seek(0)
                    w, h = content_w, ih * content_w / iw
                    max_h = 6.8 * cm      # 单图最大高度：避免大图把版面撑开、留下大片空白
                    if h > max_h:
                        h, w = max_h, iw * max_h / ih
                    block = [Image(buf, width=w, height=h)]
                    if item.get("标题"):
                        block.append(Paragraph(item["标题"], st["caption"]))
                    block.append(Spacer(1, 0.25 * cm))
                    flow.append(KeepTogether(block))
                except Exception as e:
                    log.warning("插入图片失败 %s：%s", path, e)
            elif t == "分页":
                flow.append(PageBreak())
            elif t == "间距":
                flow.append(Spacer(1, float(item.get("高度", 0.3)) * cm))

    try:
        doc.build(flow, canvasmaker=canvas_cls)
    except Exception as e:
        return False, f"原生 PDF 渲染失败：{e}"
    return (os.path.exists(pdf_path),
            f"已用内置排版引擎（reportlab）生成，含页眉页脚与图表嵌入" if os.path.exists(pdf_path)
            else "未生成 PDF")


def _pdf_sanity(path, min_kb=15, min_pages=2):
    """产物体检：避免"看似成功实为空壳"的 PDF 被采纳"""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except Exception as e:
        return False, f"无法读取产物：{e}"
    if data[:4] != b"%PDF":
        return False, "不是有效 PDF"
    if len(data) < min_kb * 1024:
        return False, f"体积过小（{len(data)//1024}KB）"
    pages = data.count(b"/Type /Page") + data.count(b"/Type/Page")
    if pages < min_pages:
        return False, f"页数异常（{pages} 页）"
    return True, f"{len(data)//1024}KB / {pages} 页"


# ============================ 统一入口 ============================
def export_pdf(docx_path, pdf_path, model=None, engine=None):
    """导出 PDF：按后端优先级尝试，返回 {ok, pdf, engine, note, tried}

    默认优先级（PDF_ENGINE=auto）：**libreoffice → native → word**，任一成功即返回，并做产物体检：
      * libreoffice：若已安装，版式与 Word 100% 一致（跨平台首选）；
      * native     ：reportlab 内置排版，~1 秒出稿、不依赖 Office，离线与演示环境最稳；
      * word       ：调用本机 Microsoft Word（有 pywin32 用 COM，没有则用系统 PowerShell COM）。
    也可用环境变量指定单一后端：auto | libreoffice | native | word
      * 想要"与 Word 完全一致"的 PDF：PDF_ENGINE=word 或 libreoffice
      * 想要"秒级出稿、绝不依赖 Office"：PDF_ENGINE=native
    """
    engine = (engine or os.environ.get("PDF_ENGINE", "auto") or "auto").lower()
    order = ["libreoffice", "native", "word"] if engine in ("auto", "") else [engine]
    tried = []
    for name in order:
        if os.path.exists(pdf_path):
            try:
                os.remove(pdf_path)
            except OSError:
                pass
        if name == "libreoffice":
            ok, note = convert_with_libreoffice(docx_path, pdf_path)
        elif name == "word":
            ok, note = convert_with_word(docx_path, pdf_path)
        elif name == "native":
            if not model:
                ok, note = False, "缺少报告数据模型，无法使用内置排版引擎"
            else:
                ok, note = render_native(model, pdf_path)
        else:
            ok, note = False, f"未知的 PDF 引擎：{name}"
        if ok and os.path.exists(pdf_path):
            good, detail = _pdf_sanity(pdf_path)
            if good:
                return {"ok": True, "pdf": pdf_path, "engine": name,
                        "note": f"{note}（{detail}）", "tried": tried + [f"{name}: {note}"]}
            note = f"{note}，但产物体检未通过：{detail}"
        tried.append(f"{name}: {note}")
    return {"ok": False, "pdf": None, "engine": None, "note": "；".join(tried),
            "tried": tried}
