"""生成 HTML 管理器 App 图标 — 渐变 + 折角文档 + <H/> 字样"""
from PIL import Image, ImageDraw, ImageFilter, ImageFont
import os

OUT_DIR = "/tmp/htmlm.iconset"
os.makedirs(OUT_DIR, exist_ok=True)

# macOS 标准 iconset 尺寸
SIZES = [
    (16, "16x16"), (32, "16x16@2x"),
    (32, "32x32"), (64, "32x32@2x"),
    (128, "128x128"), (256, "128x128@2x"),
    (256, "256x256"), (512, "256x256@2x"),
    (512, "512x512"), (1024, "512x512@2x"),
]


def lerp_color(c1, c2, t):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def rounded_rect_mask(size, radius):
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return m


def draw_icon(size: int) -> Image.Image:
    s = size
    # macOS 风格圆角（约 22% 半径）
    radius = int(s * 0.225)
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))

    # 背景：从左上 sky-400 到右下 indigo-600 的渐变
    bg = Image.new("RGB", (s, s), 0)
    px = bg.load()
    c1 = (56, 189, 248)   # sky-400
    c2 = (79, 70, 229)    # indigo-600
    for y in range(s):
        for x in range(s):
            t = (x + y) / (2 * s)
            px[x, y] = lerp_color(c1, c2, t)
    bg = bg.convert("RGBA")

    # 应用圆角 mask
    mask = rounded_rect_mask(s, radius)
    img.paste(bg, (0, 0), mask)

    # 文档卡片（白底圆角矩形 + 折角）
    doc_w = int(s * 0.56)
    doc_h = int(s * 0.66)
    doc_x = (s - doc_w) // 2
    doc_y = int(s * 0.16)
    doc_r = max(2, int(s * 0.04))
    fold = int(s * 0.16)  # 右上折角大小

    doc_layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    dd = ImageDraw.Draw(doc_layer)

    # 阴影
    shadow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle(
        (doc_x + 2, doc_y + int(s * 0.02), doc_x + doc_w + 2, doc_y + doc_h + int(s * 0.02)),
        radius=doc_r, fill=(0, 0, 0, 70),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=max(1, s // 80)))
    img = Image.alpha_composite(img, shadow)

    # 文档主体
    dd.rounded_rectangle(
        (doc_x, doc_y, doc_x + doc_w, doc_y + doc_h),
        radius=doc_r, fill=(255, 255, 255, 255),
    )
    # 切掉右上角的折角三角（覆盖透明）
    dd.polygon(
        [
            (doc_x + doc_w - fold, doc_y),
            (doc_x + doc_w, doc_y),
            (doc_x + doc_w, doc_y + fold),
        ],
        fill=(0, 0, 0, 0),
    )
    # 折角内侧：浅灰小三角作为"折回"效果
    dd.polygon(
        [
            (doc_x + doc_w - fold, doc_y),
            (doc_x + doc_w - fold, doc_y + fold),
            (doc_x + doc_w, doc_y + fold),
        ],
        fill=(220, 230, 245, 255),
    )

    img = Image.alpha_composite(img, doc_layer)

    # 文档上画 </> 风格的 H 字样
    text_layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    td = ImageDraw.Draw(text_layer)
    # 试加粗字体
    text = "</>"
    font = None
    for path, sz in [
        ("/System/Library/Fonts/SFNSMono.ttf", int(doc_h * 0.42)),
        ("/System/Library/Fonts/Menlo.ttc", int(doc_h * 0.4)),
        ("/System/Library/Fonts/Helvetica.ttc", int(doc_h * 0.42)),
    ]:
        try:
            font = ImageFont.truetype(path, sz)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()

    bbox = td.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = doc_x + (doc_w - tw) // 2 - bbox[0]
    ty = doc_y + int(doc_h * 0.18) - bbox[1]
    # 渐变同色系 indigo
    td.text((tx, ty), text, font=font, fill=(67, 56, 202, 255))

    # 文档下方画三条"列表"横线
    line_y = doc_y + int(doc_h * 0.62)
    line_pad = int(doc_w * 0.14)
    line_x1 = doc_x + line_pad
    line_x2 = doc_x + doc_w - line_pad
    line_h = max(2, int(s * 0.018))
    gap = int(s * 0.045)
    for i in range(3):
        y = line_y + i * gap
        td.rounded_rectangle(
            (line_x1, y, line_x2 - i * int(line_pad * 0.6), y + line_h),
            radius=line_h // 2,
            fill=(186, 196, 220, 255),
        )

    img = Image.alpha_composite(img, text_layer)
    return img


# 渲染最大尺寸然后下采样，质量更好
master = draw_icon(1024)
for size, label in SIZES:
    out = master.resize((size, size), Image.LANCZOS)
    out.save(os.path.join(OUT_DIR, f"icon_{label}.png"), "PNG")
    print(f"  icon_{label}.png  {size}x{size}")

print("done -> " + OUT_DIR)
