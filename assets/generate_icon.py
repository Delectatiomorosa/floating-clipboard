"""
应用图标生成脚本：生成包含 16x16, 32x32, 48x48, 256x256 多尺寸的高清深色圆角蓝白剪贴板图标 `assets/icon.ico`。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ICON_SIZES = [(16, 16), (32, 32), (48, 48), (256, 256)]


def render_clipboard_icon(size: int = 512) -> Image.Image:
    """在超大画布上超采样绘制深色圆角蓝白剪贴板图标，缩放后边缘丝滑锐利。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 1. 外层深色石墨圆角底座 + 品牌蓝渐变内衬
    pad = int(size * 0.04)
    outer_box = [pad, pad, size - pad, size - pad]
    outer_radius = int(size * 0.24)
    draw.rounded_rectangle(outer_box, radius=outer_radius, fill=(24, 27, 36, 255))

    # 渐变蓝内层圆角块
    inner_pad = int(size * 0.08)
    inner_box = [inner_pad, inner_pad, size - inner_pad, size - inner_pad]
    inner_radius = int(size * 0.20)
    # 逐行绘制柔和蓝->靛蓝垂直渐变
    grad = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    g_draw = ImageDraw.Draw(grad)
    y0, y1 = inner_box[1], inner_box[3]
    span = max(1, y1 - y0)
    for y in range(y0, y1 + 1):
        t = (y - y0) / span
        r = int(154 * (1 - t) + 102 * t)
        g = int(194 * (1 - t) + 138 * t)
        b = int(252 * (1 - t) + 245 * t)
        g_draw.line([(inner_box[0], y), (inner_box[2], y)], fill=(r, g, b, 255))

    mask = Image.new("L", (size, size), 0)
    m_draw = ImageDraw.Draw(mask)
    m_draw.rounded_rectangle(inner_box, radius=inner_radius, fill=255)
    img.paste(grad, (0, 0), mask)

    # 2. 剪贴板白色主体板面
    bx0, by0 = int(size * 0.24), int(size * 0.24)
    bx1, by1 = int(size * 0.76), int(size * 0.80)
    board_radius = int(size * 0.07)
    stroke = max(4, int(size * 0.048))
    draw.rounded_rectangle(
        [bx0, by0, bx1, by1],
        radius=board_radius,
        fill=(22, 28, 42, 215),
        outline=(255, 255, 255, 255),
        width=stroke,
    )

    # 3. 顶部金属夹扣
    cx0, cy0 = int(size * 0.36), int(size * 0.16)
    cx1, cy1 = int(size * 0.64), int(size * 0.29)
    clip_radius = int(size * 0.045)
    draw.rounded_rectangle(
        [cx0, cy0, cx1, cy1],
        radius=clip_radius,
        fill=(255, 255, 255, 255),
    )
    # 夹扣中心小槽
    hx0, hy0 = int(size * 0.44), int(size * 0.20)
    hx1, hy1 = int(size * 0.56), int(size * 0.25)
    draw.rounded_rectangle([hx0, hy0, hx1, hy1], radius=int(size * 0.02), fill=(116, 152, 246, 255))

    # 4. 板面内部两道圆角内容横条
    line_h = max(4, int(size * 0.045))
    lx0 = int(size * 0.34)
    lx1_long = int(size * 0.66)
    lx1_short = int(size * 0.56)
    ly1 = int(size * 0.45)
    ly2 = int(size * 0.59)
    draw.rounded_rectangle([lx0, ly1, lx1_long, ly1 + line_h], radius=line_h // 2, fill=(255, 255, 255, 255))
    draw.rounded_rectangle([lx0, ly2, lx1_short, ly2 + line_h], radius=line_h // 2, fill=(168, 202, 252, 255))

    return img


def generate_ico(output_path: str | Path | None = None) -> Path:
    out = Path(output_path) if output_path else Path(__file__).resolve().parent / "icon.ico"
    out.parent.mkdir(parents=True, exist_ok=True)
    master = render_clipboard_icon(512)
    master.save(str(out), format="ICO", sizes=ICON_SIZES)
    return out


if __name__ == "__main__":
    target = generate_ico()
    print(f"Generated multi-size icon: {target} ({target.stat().st_size} bytes)")
    sys.exit(0)
