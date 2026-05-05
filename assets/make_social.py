"""生成 GitHub social preview 卡片 1280x640"""
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import os

W, H = 1280, 640
OUT = "/Users/jiaozidemacmini/Documents/自制产品/html-manager/assets/social-preview.png"


def lerp(c1, c2, t):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def load_font(paths_and_sizes, fallback_size=40):
    for path, size in paths_and_sizes:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def rounded_rect_mask(w, h, r):
    m = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=r, fill=255)
    return m


# 1. 背景：从左下深 indigo 到右上亮 sky 的对角渐变
bg = Image.new("RGB", (W, H), 0)
px = bg.load()
c1 = (30, 27, 75)     # indigo-950
c2 = (14, 116, 178)   # blue-700
c3 = (56, 189, 248)   # sky-400 (高光)
for y in range(H):
    for x in range(W):
        # 主对角渐变
        t = (x + (H - y)) / (W + H)
        col = lerp(c1, c2, min(1, t * 1.2))
        # 右上角高光
        d2 = ((W - x) ** 2 + y ** 2) ** 0.5 / ((W ** 2 + H ** 2) ** 0.5)
        if d2 < 0.5:
            blend = (0.5 - d2) / 0.5 * 0.4
            col = lerp(col, c3, blend)
        px[x, y] = col

img = bg.convert("RGBA")

# 2. 装饰背景：多个半透明大圆模糊
deco = Image.new("RGBA", (W, H), (0, 0, 0, 0))
dd = ImageDraw.Draw(deco)
dd.ellipse((-200, -200, 400, 400), fill=(56, 189, 248, 60))
dd.ellipse((900, 350, 1500, 950), fill=(167, 139, 250, 50))
deco = deco.filter(ImageFilter.GaussianBlur(radius=80))
img = Image.alpha_composite(img, deco)

# 3. 大块文字 - 左侧
draw = ImageDraw.Draw(img)
font_title = load_font([
    ("/System/Library/Fonts/STHeiti Medium.ttc", 92),
    ("/System/Library/Fonts/Hiragino Sans GB.ttc", 92),
])
font_subtitle = load_font([
    ("/System/Library/Fonts/STHeiti Medium.ttc", 34),
    ("/System/Library/Fonts/Hiragino Sans GB.ttc", 34),
])
font_url = load_font([
    ("/System/Library/Fonts/Menlo.ttc", 24),
    ("/System/Library/Fonts/SFNSMono.ttf", 24),
])
font_chip = load_font([
    ("/System/Library/Fonts/STHeiti Medium.ttc", 24),
    ("/System/Library/Fonts/Hiragino Sans GB.ttc", 24),
])

# 主标题
draw.text((80, 130), "HTML 管理器", font=font_title, fill=(255, 255, 255, 255))
# 副标题（两行）
draw.text((80, 240),
          "把电脑里散落的所有 HTML 文件",
          font=font_subtitle, fill=(220, 230, 245, 240))
draw.text((80, 285),
          "像文档一样集中管理",
          font=font_subtitle, fill=(220, 230, 245, 240))

# 特性 chips（小圆角胶囊）
chips = ["零依赖", "全盘扫描", "重复检测", "标签分类", "本地运行"]
chip_y = 380
chip_x = 80
chip_pad_x = 18
chip_pad_y = 9
chip_gap = 12
for text in chips:
    bbox = draw.textbbox((0, 0), text, font=font_chip)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    cw = tw + chip_pad_x * 2
    ch = th + chip_pad_y * 2 + 4
    chip = Image.new("RGBA", (int(cw), int(ch)), (0, 0, 0, 0))
    cd = ImageDraw.Draw(chip)
    cd.rounded_rectangle((0, 0, cw - 1, ch - 1), radius=ch // 2,
                         fill=(255, 255, 255, 35), outline=(255, 255, 255, 120), width=1)
    cd.text((chip_pad_x - bbox[0], chip_pad_y - bbox[1]), text,
            font=font_chip, fill=(255, 255, 255, 255))
    img.alpha_composite(chip, (int(chip_x), int(chip_y)))
    chip_x += cw + chip_gap

# 4. URL 底部（github 风格）
draw.text((80, 530), "github.com/jiaoziluanbu/html-manager",
          font=font_url, fill=(186, 230, 253, 220))

# 5. 右侧图标 — 重用 make_icon.py 的逻辑生成大图标
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def draw_icon(s):
    """简化版图标绘制"""
    radius = int(s * 0.225)
    icon = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    bg = Image.new("RGB", (s, s), 0)
    bpx = bg.load()
    ic1 = (56, 189, 248)
    ic2 = (79, 70, 229)
    for y in range(s):
        for x in range(s):
            t = (x + y) / (2 * s)
            bpx[x, y] = lerp(ic1, ic2, t)
    bg = bg.convert("RGBA")
    icon.paste(bg, (0, 0), rounded_rect_mask(s, s, radius))

    doc_w = int(s * 0.56)
    doc_h = int(s * 0.66)
    doc_x = (s - doc_w) // 2
    doc_y = int(s * 0.16)
    doc_r = max(2, int(s * 0.04))
    fold = int(s * 0.16)

    layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    # shadow
    sh = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sh)
    sd.rounded_rectangle(
        (doc_x + 4, doc_y + int(s * 0.025), doc_x + doc_w + 4, doc_y + doc_h + int(s * 0.025)),
        radius=doc_r, fill=(0, 0, 0, 90))
    sh = sh.filter(ImageFilter.GaussianBlur(radius=max(2, s // 60)))
    icon = Image.alpha_composite(icon, sh)

    ld.rounded_rectangle((doc_x, doc_y, doc_x + doc_w, doc_y + doc_h),
                         radius=doc_r, fill=(255, 255, 255, 255))
    ld.polygon(
        [(doc_x + doc_w - fold, doc_y), (doc_x + doc_w, doc_y), (doc_x + doc_w, doc_y + fold)],
        fill=(0, 0, 0, 0))
    ld.polygon(
        [(doc_x + doc_w - fold, doc_y), (doc_x + doc_w - fold, doc_y + fold), (doc_x + doc_w, doc_y + fold)],
        fill=(220, 230, 245, 255))
    icon = Image.alpha_composite(icon, layer)

    text = "</>"
    f_doc = load_font([
        ("/System/Library/Fonts/SFNSMono.ttf", int(doc_h * 0.42)),
        ("/System/Library/Fonts/Menlo.ttc", int(doc_h * 0.4)),
    ])
    txt_layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    td = ImageDraw.Draw(txt_layer)
    bbox = td.textbbox((0, 0), text, font=f_doc)
    tw = bbox[2] - bbox[0]
    tx = doc_x + (doc_w - tw) // 2 - bbox[0]
    ty = doc_y + int(doc_h * 0.18) - bbox[1]
    td.text((tx, ty), text, font=f_doc, fill=(67, 56, 202, 255))

    line_y = doc_y + int(doc_h * 0.62)
    line_pad = int(doc_w * 0.14)
    line_x1 = doc_x + line_pad
    line_x2 = doc_x + doc_w - line_pad
    line_h = max(2, int(s * 0.018))
    gap = int(s * 0.045)
    for i in range(3):
        y = line_y + i * gap
        td.rounded_rectangle((line_x1, y, line_x2 - i * int(line_pad * 0.6), y + line_h),
                             radius=line_h // 2, fill=(186, 196, 220, 255))
    icon = Image.alpha_composite(icon, txt_layer)
    return icon


icon_size = 380
icon = draw_icon(icon_size)
# 给图标加阴影
icon_shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
sh_layer = Image.new("RGBA", (icon_size + 80, icon_size + 80), (0, 0, 0, 0))
shd = ImageDraw.Draw(sh_layer)
shd.rounded_rectangle((40, 50, icon_size + 30, icon_size + 50),
                       radius=int(icon_size * 0.225), fill=(0, 0, 0, 110))
sh_layer = sh_layer.filter(ImageFilter.GaussianBlur(radius=25))
icon_pos = (W - icon_size - 110, (H - icon_size) // 2)
icon_shadow.paste(sh_layer, (icon_pos[0] - 40, icon_pos[1] - 30), sh_layer)
img = Image.alpha_composite(img, icon_shadow)
img.alpha_composite(icon, icon_pos)

img.convert("RGB").save(OUT, "PNG", optimize=True)
print(f"✅ 生成 {OUT}")
print(f"   尺寸: {W}x{H}, 文件大小: {os.path.getsize(OUT)/1024:.1f} KB")
