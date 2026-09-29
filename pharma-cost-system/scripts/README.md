# scripts 目录索引

本目录脚本按用途分为四类，全部可用 `python scripts/<类别>/<脚本>.py` 在项目根直接运行。
脚本会自动把项目根与 `vendor/`（若存在）加入 `sys.path`，无需额外配置环境变量。

> 运行中文输出建议先执行（Windows PowerShell）：`$env:PYTHONIOENCODING='utf-8'`

---

## 一、`verify/` —— 自验与评测（可复现证据）

| 脚本 | 作用 | 主要产出 |
|---|---|---|
| `verify_period.py` | 周期聚合口径对拍：月度聚合路径 vs 既有 `data_service` 月度口径（全月份 × 全产品逐字段） | 控制台对拍结果，异常非零退出 |
| `verify_report.py` | 报告双格式导出自检：3 个分析主题 × Word/PDF，检查占位符零残留、表格/图表数、页眉页脚 | 控制台自检表 + `outputs/report_*` |
| `verify_api.py` | 接口全链路：元信息 → 生成报告 → 下载 Word/PDF/图表 → 归档 → 非法参数拦截 | 控制台清单（含 PDF 引擎） |
| `verify_rag.py` | **RAG 混合检索评测**：BM25 / 向量 / 混合（含 RRF）三模式 Hit@1、Hit@3、MRR 对比 + alpha 扫描 + 图谱增强消融 + PDF/Word/TXT 多格式接入自检 | `outputs/eval/rag_eval.json` |
| `verify_benchmark.py` | **对标差异计算准确率独立复算**（pandas 直读官方 CSV，不复用被测函数）+ 三步法输出格式正确率 | `outputs/eval/benchmark_accuracy.json` |
| `verify_rca.py` | **归因文本质量评分卡**：6 项可判定检查（要素/金额幅度/贡献度/知识库引用/可执行建议/实体），规则引擎 vs 大模型对比 | `outputs/eval/rca_quality.json` |

## 二、`eval/` —— 评测报告与交付物生成

| 脚本 | 作用 | 主要产出 |
|---|---|---|
| `gen_eval_report.py` | 汇总 `verify_*` 证据与三场景真实运行结果，生成《评测报告》Word（报告结构完整度、归因合理性、RPA 触发成功率、三步法格式正确率） | `02_参赛文档/评测报告-*.docx` + `outputs/eval/eval_summary.json` |

## 三、`tools/` —— 工程与排障辅助

| 脚本 | 作用 |
|---|---|
| `fetch_vendor.py` | 受限网络/离线部署：逐个镜像重试下载 wheel 并解包到项目内 `vendor/`（`app/__init__.py` 自动加载） |
| `report_summary.py` | 交付物清单：列出 `outputs/` 各报告的页数、表格数、图表数、页眉页脚与图表原图清单 |
| `preview_pdf.py` | 把 PDF 渲染成 PNG（依赖 `pypdfium2`，见 vendor），便于人工检查排版 / 截图留证 |
| `check_frontend.py` | 前端自检：内联 JS 语法（node --check）、`$('id')` 引用一致性、ECharts 容器、div 开合 |
| `scan_text_noise.py` | 扫描知识库文本里的"部首/兼容字符"噪声（PDF 抽取噪声），输出正则还原建议 |

## 四、`data/` —— 原始数据/模板生成脚本（历史保留）

| 脚本 | 作用 | 说明 |
|---|---|---|
| `generate_data.py` | 生成自制模拟成本数据（`data/*.csv`） | 开发期产物；当前演示主流程使用官方数据 `data_official/`，本脚本仅在需要离线自造数据时使用 |
| `generate_template.py` | 生成自制 Word 报告模板（`data/template/report_template.docx`） | 备用模板；主流程使用官方模板 `data/template_official.docx` |

---

## 典型使用顺序

```bash
# 1) 回归与口径（改代码后必跑）
python scripts/verify/verify_period.py
python scripts/verify/verify_report.py

# 2) 评测证据（生成评测报告前跑，结果写入 outputs/eval/）
python scripts/verify/verify_rag.py
python scripts/verify/verify_benchmark.py
python scripts/verify/verify_rca.py

# 3) 接口全链路
python scripts/verify/verify_api.py

# 4) 生成《评测报告》与交付物清单
python scripts/eval/gen_eval_report.py
python scripts/tools/report_summary.py
```
