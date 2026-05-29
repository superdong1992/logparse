#!/usr/bin/env python3
"""生成项目工作流程图 (SVG)，零外部依赖。"""
from __future__ import annotations

import html

# ── 配色 ──
C_BG       = "#1a1a2e"
C_CARD     = "#16213e"
C_BORDER   = "#0f3460"
C_TEXT     = "#e0e0e0"
C_ACCENT   = "#e94560"
C_PURPLE   = "#533483"
C_BLUE     = "#1a508b"
C_GOLD     = "#f0a500"
C_GREEN    = "#2ecc71"
C_DIM      = "#888"

# ── 布局参数 ──
BOX_W, BOX_H = 200, 54
GAP_X, GAP_Y = 40, 30
MARGIN = 30
HEADER_H = 60
ARROW_COLOR = "#533483"

SVG_W = 1100
SVG_H = 1050


def _esc(s: str) -> str:
    return html.escape(s)


def box(x: float, y: float, w: float, h: float, label: str,
        fill: str = C_CARD, border: str = C_BORDER, text_color: str = C_TEXT,
        rx: int = 8, font_size: int = 13, bold: bool = False) -> str:
    lines = label.split("\n")
    weight = "bold" if bold else "normal"
    text_parts = []
    for i, line in enumerate(lines):
        dy = (i - (len(lines) - 1) / 2) * (font_size + 4)
        text_parts.append(
            f'<text x="{x + w/2}" y="{y + h/2 + dy}" text-anchor="middle" '
            f'fill="{text_color}" font-size="{font_size}" font-weight="{weight}" '
            f'font-family="system-ui,sans-serif">{_esc(line)}</text>'
        )
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" '
        f'fill="{fill}" stroke="{border}" stroke-width="1.5"/>'
        + "".join(text_parts)
    )


def arrow(x1: float, y1: float, x2: float, y2: float, label: str = "") -> str:
    parts = [f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
             f'stroke="{ARROW_COLOR}" stroke-width="2" marker-end="url(#arrow)"/>']
    if label:
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        parts.append(
            f'<text x="{mx + 8}" y="{my}" fill="{C_GOLD}" font-size="11" '
            f'font-family="system-ui,sans-serif">{_esc(label)}</text>'
        )
    return "".join(parts)


def section_label(x: float, y: float, text: str) -> str:
    return (f'<text x="{x}" y="{y}" fill="{C_DIM}" font-size="11" '
            f'font-family="system-ui,sans-serif" font-style="italic">'
            f'{_esc(text)}</text>')


def generate() -> str:
    parts: list[str] = []

    # ── Defs ──
    parts.append(f'''<defs>
  <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5"
    markerWidth="8" markerHeight="8" orient="auto">
    <path d="M 0 0 L 10 5 L 0 10 z" fill="{ARROW_COLOR}"/>
  </marker>
  <linearGradient id="headerGrad" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0%" stop-color="#0f3460"/>
    <stop offset="100%" stop-color="#533483"/>
  </linearGradient>
</defs>''')

    # ── 标题 ──
    parts.append(
        f'<rect x="0" y="0" width="{SVG_W}" height="{HEADER_H}" fill="url(#headerGrad)"/>'
        f'<text x="{SVG_W/2}" y="28" text-anchor="middle" fill="white" font-size="22" '
        f'font-weight="bold" font-family="system-ui,sans-serif">logparse 工作流程</text>'
        f'<text x="{SVG_W/2}" y="48" text-anchor="middle" fill="#aaa" font-size="12" '
        f'font-family="system-ui,sans-serif">日志压缩包预处理管道 — 插件化架构</text>'
    )

    # ── 列坐标 ──
    cx = SVG_W / 2
    left_x = cx - BOX_W / 2 - 220
    right_x = cx + BOX_W / 2 + 20

    # ── 行坐标 ──
    y = HEADER_H + 30
    row_h = BOX_H + GAP_Y

    # Row 0: 输入
    parts.append(box(cx - BOX_W/2, y, BOX_W, BOX_H, "📦 日志压缩包\n.zip / .tar.gz",
                     fill=C_BLUE, border=C_GOLD, text_color="white", bold=True))

    # Row 1: Step 1
    y += row_h
    s1x, s1y = cx - BOX_W/2, y
    parts.append(section_label(s1x - 10, s1y - 5, "Step 1"))
    parts.append(box(s1x, s1y, BOX_W, BOX_H, "Decompressor\n统一解压归档\n普通 .gz 默认保留", font_size=12))
    parts.append(arrow(cx, s1y - GAP_Y + BOX_H, cx, s1y))

    # Row 2: Step 2 — 目录发现（分支）
    y += row_h
    s2y = y
    parts.append(section_label(cx - BOX_W/2 - 10, s2y - 5, "Step 2"))
    parts.append(box(cx - BOX_W/2, s2y, BOX_W, BOX_H, "DirectoryDiscovery\nPlugin 📂",
                     fill=C_PURPLE, border="#7c4dff", text_color="white", bold=True))
    parts.append(arrow(cx, s1y + BOX_H, cx, s2y))

    # 分支: default
    bw, bh = 180, 44
    branch_y = s2y + row_h + 5
    parts.append(box(left_x, branch_y, bw, bh, "ScannerPlugin\n(default)\ndiag/ + varlog/",
                     fill=C_CARD, border=C_BLUE))
    parts.append(arrow(cx - BOX_W/2, s2y + BOX_H/2, left_x + bw, branch_y + bh/2, "default"))

    # 分支: compact
    parts.append(box(right_x, branch_y, bw, bh, "CompactScanner\n(compact)\nboards/ + logs/",
                     fill=C_CARD, border=C_BLUE))
    parts.append(arrow(cx + BOX_W/2, s2y + BOX_H/2, right_x, branch_y + bh/2, "compact"))

    # Row 3: Step 3 — no separate middle extraction stage
    y = branch_y + bh + GAP_Y
    s3x, s3y = cx - BOX_W/2, y
    parts.append(section_label(s3x - 10, s3y - 5, "Step 3"))
    parts.append(box(s3x, s3y, BOX_W, BOX_H, "无中间解压阶段\nScanner 只扫描工作区", font_size=12))
    # arrows from branches
    parts.append(arrow(left_x + bw/2, branch_y + bh, cx - 30, s3y))
    parts.append(arrow(right_x + bw/2, branch_y + bh, cx + 30, s3y))

    # Row 4: Step 4 — 解析（核心）
    y += row_h
    s4x, s4y = cx - BOX_W/2, y
    parts.append(section_label(s4x - 10, s4y - 5, "Step 4  🔍"))
    parts.append(box(s4x, s4y, BOX_W, BOX_H, "ParserPlugin\n解析编排层",
                     fill=C_ACCENT, border="#c81e45", text_color="white", bold=True))
    parts.append(arrow(cx, s3y + BOX_H, cx, s4y))

    # 子组件
    sub_y = s4y + row_h + 5
    sub_w, sub_h = 175, 38

    # TimestampExtractor
    ts_x = left_x - 10
    parts.append(box(ts_x, sub_y, sub_w, sub_h,
                     "TimestampExtractor\n时间戳提取 + 时区对齐",
                     fill="#2d1b69", border=C_PURPLE, text_color=C_TEXT, font_size=11))
    parts.append(arrow(s4x, s4y + BOX_H, ts_x + sub_w/2, sub_y))

    # CycleDetector
    cd_x = cx - sub_w/2
    parts.append(box(cd_x, sub_y, sub_w, sub_h,
                     "CycleDetector\nPID变化 + 序号回绕切分",
                     fill="#2d1b69", border=C_PURPLE, text_color=C_TEXT, font_size=11))
    parts.append(arrow(cx, s4y + BOX_H, cd_x + sub_w/2, sub_y))

    # RoleIdentifier
    ri_x = right_x + 10
    parts.append(box(ri_x, sub_y, sub_w, sub_h,
                     "RoleIdentifier\n机制优先 + 兜底判定",
                     fill="#2d1b69", border=C_PURPLE, text_color=C_TEXT, font_size=11))
    parts.append(arrow(s4x + BOX_W, s4y + BOX_H, ri_x + sub_w/2, sub_y))

    # Row 5+6: Step 5 + Step 6 并排
    y = sub_y + sub_h + GAP_Y + 10

    out_w = 220
    # Step 5: 落盘
    parts.append(section_label(left_x - 10, y - 5, "Step 5"))
    parts.append(box(left_x, y, out_w, BOX_H, "MechOutputWriter\nslot/board_cycle/\n[cpu_N/cpu_cycle/]proc.log",
                     fill=C_CARD, border=C_GREEN, text_color=C_TEXT, font_size=12))
    parts.append(arrow(ts_x + sub_w/2, sub_y + sub_h, left_x + out_w/2 - 30, y))
    parts.append(arrow(cd_x + sub_w/2, sub_y + sub_h, left_x + out_w/2 + 30, y))

    # Step 6: 元数据
    parts.append(section_label(right_x + 10, y - 5, "Step 6"))
    parts.append(box(right_x, y, out_w, BOX_H, "MetadataGenerator\nmetadata.json\nresult.json",
                     fill=C_CARD, border=C_GOLD, text_color=C_TEXT))
    parts.append(arrow(ri_x + sub_w/2, sub_y + sub_h, right_x + out_w/2, y))

    # 输出
    y += BOX_H + GAP_Y + 10
    parts.append(box(cx - BOX_W/2 - 20, y, BOX_W + 40, BOX_H, "📁 output/{task_id}/",
                     fill=C_BLUE, border=C_GOLD, text_color="white", bold=True))
    parts.append(arrow(left_x + out_w/2, y - GAP_Y - 10 + BOX_H, cx - 30, y))
    parts.append(arrow(right_x + out_w/2, y - GAP_Y - 10 + BOX_H, cx + 30, y))

    # ── 图例 ──
    y += BOX_H + 30
    leg_w = 500
    leg_h = 110
    leg_x = (SVG_W - leg_w) / 2
    parts.append(f'<rect x="{leg_x}" y="{y}" width="{leg_w}" height="{leg_h}" rx="6" '
                 f'fill="{C_CARD}" stroke="{C_BORDER}"/>')
    parts.append(f'<text x="{leg_x + 15}" y="{y + 22}" fill="{C_TEXT}" font-size="13" '
                 f'font-weight="bold" font-family="system-ui,sans-serif">模块职责速查</text>')

    legends = [
        (C_ACCENT, "ParserPlugin — 解析编排，委托给 3 个子组件"),
        (C_PURPLE, "TimestampExtractor / CycleDetector / RoleIdentifier — 核心解析组件"),
        (C_GREEN, "MechOutputWriter — slot/board_cycle/[cpu_N/cpu_cycle/] 嵌套落盘"),
        (C_GOLD, "MetadataGenerator — 输出 metadata.json"),
    ]
    for i, (color, text) in enumerate(legends):
        ly = y + 42 + i * 18
        parts.append(f'<circle cx="{leg_x + 25}" cy="{ly - 4}" r="5" fill="{color}"/>')
        parts.append(f'<text x="{leg_x + 40}" y="{ly}" fill="{C_TEXT}" font-size="11" '
                     f'font-family="system-ui,sans-serif">{_esc(text)}</text>')

    # ── 组装 ──
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{SVG_W}" height="{SVG_H}" '
        f'viewBox="0 0 {SVG_W} {SVG_H}">\n'
        f'<rect width="100%" height="100%" fill="{C_BG}"/>\n'
        + "\n".join(parts)
        + "\n</svg>"
    )
    return "\n".join(line.rstrip() for line in svg.splitlines()) + "\n"


if __name__ == "__main__":
    from pathlib import Path
    out = Path(__file__).parent / "workflow.svg"
    out.write_text(generate(), encoding="utf-8", newline="\n")
    print(f"已生成: {out}")
