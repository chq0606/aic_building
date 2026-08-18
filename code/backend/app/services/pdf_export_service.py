"""
节能方案 PDF 导出服务: matplotlib 画图 + Jinja2 渲染 HTML + weasyprint 转 PDF。

Windows conda env 下 weasyprint 必须先注入 Library/bin 到 PATH, 否则
cffi 找不到 pango/cairo DLL。这里在模块顶部自动检测 sys.prefix 拼 Library/bin,
有则加到 PATH, 没有就跳过 (Linux/Mac 不需要)。

中文字体: matplotlib 用 Microsoft YaHei, weasyprint 用系统字体 (Windows 自带
微软雅黑 + 宋体)。weasyprint 通过 fontconfig 找字体, 不需要手动指定。

PDF 结构 (跟 templates/optimization_report.html 对齐):
  - 封面: 项目名 + 楼名 + 日期 + 版本
  - 第一段: 整体评价 summary
  - 第二段: 问题清单 problems (表格)
  - 第三段: 节能措施 measures (表格)
  - 第四段: 优先级 top 3 priorities
  - 附录: EUI 趋势图 + 异常分布图 (matplotlib 生成)
  - 附录: 引用条款原文 (从 citations 抽 document 类型)
"""
from __future__ import annotations

import base64
import io
import os
import sys
from datetime import datetime
from pathlib import Path

from loguru import logger

from app.core.config import settings

# ---------------------------------------------------------------------------
# DLL PATH 注入 (Windows conda env)
# 必须在 import weasyprint 之前做, 否则 cffi dlopen 找不到 pango。
# sys.prefix 在 conda env 里指向 env 根目录, env 根目录下 Library/bin
# 是 conda-forge 装的 DLL 位置。
# ---------------------------------------------------------------------------

_conda_bin = os.path.join(sys.prefix, "Library", "bin")
if sys.platform == "win32" and os.path.isdir(_conda_bin):
    os.environ["PATH"] = _conda_bin + os.pathsep + os.environ.get("PATH", "")
    logger.debug("weasyprint DLL PATH 注入: {}", _conda_bin)

import matplotlib
matplotlib.use("Agg")  # 无 GUI 后端, 服务端必备
import matplotlib.pyplot as plt
import jinja2

# weasyprint 在 import 时会 dlopen pango/cairo, 上面 PATH 已注入
import weasyprint


# ---------------------------------------------------------------------------
# matplotlib 中文字体配置
# ---------------------------------------------------------------------------

# Windows 自带的中文字体优先级: 微软雅黑 (现代) > 黑体 (老) > 宋体 (备选)
# Linux 服务器没装中文字体会显示方块, 这是已知限制, 部署文档里说明
_MATPLOTLIB_FONTS = ["Microsoft YaHei", "SimHei", "SimSun", "DejaVu Sans"]
plt.rcParams["font.sans-serif"] = _MATPLOTLIB_FONTS
plt.rcParams["axes.unicode_minus"] = False  # 负号显示, 中文环境下要关
plt.rcParams["figure.dpi"] = 120
plt.rcParams["savefig.bbox"] = "tight"


# ---------------------------------------------------------------------------
# 模板加载
# ---------------------------------------------------------------------------

# templates 目录在 backend/app/templates/, 跟 pdf_export_service.py 同 package
_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
_jinja_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=jinja2.select_autoescape(["html", "xml"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


# ---------------------------------------------------------------------------
# 颜色 (跟前端 tokens.scss 对齐, PDF 也走暖灰琥珀色板)
# ---------------------------------------------------------------------------

_COLOR_AMBER = "#D49B3B"
_COLOR_AMBER_DEEP = "#A06A1A"
_COLOR_GREEN = "#3D7E6A"
_COLOR_RED = "#C75D5D"
_COLOR_ORANGE = "#D8804B"
_COLOR_TEXT = "#4A4A4A"
_COLOR_PAPER = "#F5F2EB"
_COLOR_BORDER = "#E0DCD3"


# ---------------------------------------------------------------------------
# 图表渲染 (matplotlib -> base64 PNG)
# ---------------------------------------------------------------------------


def render_eui_trend(daily_series: list[dict], building_name: str) -> str | None:
    """渲染 EUI 趋势图, 返 base64 编码的 PNG。

    daily_series: [{date, kwh}] 列表, 取最近 30 个点。
    没数据返 None, 模板里跳过图。
    """
    if not daily_series:
        return None

    # 取最近 30 个点, 太多 PDF 里挤
    series = daily_series[-30:]
    dates = [p.get("date", "") for p in series]
    values = [float(p.get("kwh", 0) or 0) for p in series]

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(dates, values, color=_COLOR_AMBER, marker="o", markersize=4, linewidth=1.5)
    ax.fill_between(dates, values, alpha=0.15, color=_COLOR_AMBER)
    ax.set_title(f"{building_name} 能耗趋势", fontsize=13, color=_COLOR_TEXT, pad=12)
    ax.set_xlabel("日期", fontsize=10, color=_COLOR_TEXT)
    ax.set_ylabel("能耗 (kWh)", fontsize=10, color=_COLOR_TEXT)
    ax.grid(True, alpha=0.3, linestyle="--")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(axis="x", rotation=30, labelsize=8, colors=_COLOR_TEXT)
    ax.tick_params(axis="y", labelsize=9, colors=_COLOR_TEXT)

    # x 轴标签太多时隔 5 个显示一个, 避免重叠
    if len(dates) > 10:
        for i, label in enumerate(ax.get_xticklabels()):
            if i % 5 != 0:
                label.set_visible(False)

    png_b64 = _fig_to_base64(fig)
    plt.close(fig)
    return png_b64


def render_anomaly_distribution(anomalies: list[dict]) -> str | None:
    """渲染异常分布图 (按 event_type 柱状图), 返 base64 PNG。"""
    if not anomalies:
        return None

    # 按 event_type 聚合
    type_count: dict[str, int] = {}
    for a in anomalies:
        et = a.get("event_type", "UNKNOWN")
        type_count[et] = type_count.get(et, 0) + 1

    types = list(type_count.keys())
    counts = list(type_count.values())

    fig, ax = plt.subplots(figsize=(10, 4))
    bars = ax.bar(types, counts, color=[
        _COLOR_RED if t == "BASELINE_DEVIATION" else _COLOR_AMBER for t in types
    ])
    ax.set_title("异常事件分布", fontsize=13, color=_COLOR_TEXT, pad=12)
    ax.set_xlabel("异常类型", fontsize=10, color=_COLOR_TEXT)
    ax.set_ylabel("事件数", fontsize=10, color=_COLOR_TEXT)
    ax.grid(True, alpha=0.3, linestyle="--", axis="y")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(axis="x", labelsize=9, colors=_COLOR_TEXT)
    ax.tick_params(axis="y", labelsize=9, colors=_COLOR_TEXT)

    # 柱顶标数字
    for bar, c in zip(bars, counts):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.1,
                str(c), ha="center", va="bottom", fontsize=10, color=_COLOR_TEXT)

    png_b64 = _fig_to_base64(fig)
    plt.close(fig)
    return png_b64


def _fig_to_base64(fig) -> str:
    """matplotlib figure -> base64 PNG 字符串 (无 data: 前缀)。"""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight", facecolor="white")
    return base64.b64encode(buf.getvalue()).decode("ascii")


# ---------------------------------------------------------------------------
# 从 message 抽数据给模板
# ---------------------------------------------------------------------------


def _extract_data_for_pdf(message: dict) -> dict:
    """从 assistant_message 抽数据给 PDF 模板用。

    message 字段:
      - content: 文本回答
      - tool_calls: 工具调用摘要 [{id, name, arguments, ok}]
      - citations: 引用证据 [{type, ...}]
      - optimization_plan: 四段结构化 JSON
    """
    plan = message.get("optimization_plan") or {}
    citations = message.get("citations") or []
    tool_calls = message.get("tool_calls") or []

    # 从 citations 拿楼名 / EUI 时序 / 异常列表
    building_name = ""
    daily_series: list[dict] = []
    anomalies: list[dict] = []
    park_overview: dict | None = None
    document_refs: list[dict] = []

    for c in citations:
        ctype = c.get("type")
        if ctype == "data":
            if c.get("building_name"):
                building_name = c["building_name"]
            if c.get("stats"):
                # query_building_energy 的 stats, 没有日时序点, 跳过
                pass
            if c.get("scope") == "park":
                park_overview = c
        elif ctype == "anomaly":
            anomalies.append(c)
        elif ctype == "document":
            document_refs.append(c)

    # 没 building_name 用 park_overview 或默认
    if not building_name and park_overview:
        building_name = "园区整体"
    if not building_name:
        building_name = "未指定建筑"

    return {
        "building_name": building_name,
        "plan": plan,
        "summary": plan.get("summary", ""),
        "problems": plan.get("problems", []),
        "measures": plan.get("measures", []),
        "priorities": plan.get("priorities", []),
        "daily_series": daily_series,
        "anomalies": anomalies,
        "park_overview": park_overview,
        "document_refs": document_refs,
        "tool_calls": tool_calls,
    }


# ---------------------------------------------------------------------------
# 主导出函数
# ---------------------------------------------------------------------------


def export_optimization_pdf(message_id: str, message: dict, tenant_id: str) -> tuple[bytes, str]:
    """渲染节能方案 PDF, 返 (pdf_bytes, filename)。

    流程:
      1. 从 message 抽数据
      2. matplotlib 生成 EUI 趋势图 + 异常分布图 (base64)
      3. Jinja2 渲染 optimization_report.html
      4. weasyprint.HTML(string=html).write_pdf() 返 bytes
      5. 文件名: 节能方案_{building_name}_{date}.pdf
    """
    data = _extract_data_for_pdf(message)

    # 生成图表
    eui_chart_b64 = render_eui_trend(data["daily_series"], data["building_name"])
    anomaly_chart_b64 = render_anomaly_distribution(data["anomalies"])

    # 渲染 HTML
    today = datetime.now().strftime("%Y-%m-%d")
    template = _jinja_env.get_template("optimization_report.html")
    html = template.render(
        app_name="建筑能耗分析与节能优化平台",
        app_version=settings.app_version,
        building_name=data["building_name"],
        date=today,
        summary=data["summary"],
        problems=data["problems"],
        measures=data["measures"],
        priorities=data["priorities"],
        document_refs=data["document_refs"],
        park_overview=data["park_overview"],
        eui_chart_b64=eui_chart_b64,
        anomaly_chart_b64=anomaly_chart_b64,
        color_amber=_COLOR_AMBER,
        color_amber_deep=_COLOR_AMBER_DEEP,
        color_green=_COLOR_GREEN,
        color_red=_COLOR_RED,
        color_orange=_COLOR_ORANGE,
        color_text=_COLOR_TEXT,
        color_paper=_COLOR_PAPER,
        color_border=_COLOR_BORDER,
    )

    logger.info(
        "PDF 渲染开始 message_id={} building={} measures={}",
        message_id[:8], data["building_name"], len(data["measures"]),
    )

    # weasyprint 转 PDF
    pdf_bytes = weasyprint.HTML(string=html).write_pdf()
    logger.info("PDF 渲染完成 size={} bytes", len(pdf_bytes))

    # 文件名: 节能方案_{building_name}_{date}.pdf
    safe_name = "".join(c for c in data["building_name"] if c.isalnum() or c in "_-") or "building"
    filename = f"节能方案_{safe_name}_{today}.pdf"
    return pdf_bytes, filename


__all__ = ["export_optimization_pdf"]
