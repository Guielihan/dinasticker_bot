# quote_maker.py
from PIL import Image, ImageDraw, ImageFont, ImageOps
import io, math

# tenta carregar uma fonte decente; ajuste o caminho se quiser uma TTF no repo (ex: assets/fonts/Inter-Regular.ttf)
def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arial.ttf", size)  # windows costuma ter
    except Exception:
        return ImageFont.load_default()

def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_w: int):
    words = text.replace("\r","").split()
    lines, cur = [], ""
    for w in words:
        test = (cur + " " + w).strip()
        # mede a largura do texto compatível com Pillow novo
        try:
            bbox = draw.textbbox((0, 0), test, font=font)
            w_px = bbox[2] - bbox[0]
        except AttributeError:
            # fallback pra versões mais antigas que ainda tem textsize
            w_px, _ = draw.textsize(test, font=font)
        if w_px <= max_w or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines

def _circle_avatar(avatar_img: Image.Image, size: int) -> Image.Image:
    av = avatar_img.convert("RGBA").resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    d.ellipse((0,0,size,size), fill=255)
    av.putalpha(mask)
    return av

def make_quote_sticker(
    text: str,
    author_name: str | None = None,
    avatar_img: Image.Image | None = None,
    *,
    theme: str = "dark",      # "dark" | "light"
    bg_hex: str | None = None, # cor da bolha (ex: "#222834")
    txt_hex: str | None = None, # cor do texto
    show_avatar: bool = True,
    canvas_size: int = 512,
) -> bytes:
    # canvas transparente 512x512
    W = H = canvas_size
    out = Image.new("RGBA", (W, H), (0,0,0,0))
    draw = ImageDraw.Draw(out)

    # cores básicas
    if theme == "light":
        bubble = (245, 246, 248, 255)
        fg = (25, 25, 28, 255)
        meta = (90, 90, 95, 255)
    else:
        bubble = (34, 40, 52, 255)
        fg = (245, 246, 248, 255)
        meta = (170, 175, 186, 255)

    if bg_hex:
        bg_hex = bg_hex.lstrip("#")
        bubble = tuple(int(bg_hex[i:i+2], 16) for i in (0,2,4)) + (255,)
    if txt_hex:
        txt_hex = txt_hex.lstrip("#")
        fg = tuple(int(txt_hex[i:i+2], 16) for i in (0,2,4)) + (255,)

    # margens e layout
    P = 24
    AV = 72 if (show_avatar and avatar_img) else 0
    GAP = 16 if AV else 0
    max_text_w = W - (P*2) - AV - GAP

    font_name = _load_font(20)
    font_text = _load_font(24)

    # quebra de linhas
    lines = _wrap_text(draw, text, font_text, max_text_w)
    line_h = font_text.getbbox("Ag")[3] 
    block_h = len(lines)*line_h + (8*(len(lines)-1) if len(lines)>0 else 0)

    name_h = 0
    if author_name:
        name_h = font_name.getbbox("Ag")[3] + 8

    box_h = max(block_h + name_h, AV)
    radius = 24

    # bolha
    x0 = P
    y0 = (H - box_h)//2
    x1 = W - P
    y1 = y0 + box_h
    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=bubble)

    # avatar
    cur_x = x0 + 16
    cur_y = y0 + 16
    if AV:
        av = _circle_avatar(avatar_img, AV)
        out.alpha_composite(av, (cur_x, y0 + (box_h-AV)//2))
        cur_x += AV + GAP

    # Nome
    if author_name:
        draw.text((cur_x, cur_y), author_name, font=font_name, fill=meta)
        cur_y += font_name.getbbox("Ag")[3] + 6

    # texto
    for i, ln in enumerate(lines):
        draw.text((cur_x, cur_y + i*(line_h+8)), ln, font=font_text, fill=fg)

    # exporta WEBP
    bio = io.BytesIO()
    out.save(bio, "WEBP", quality=95, method=6)
    bio.seek(0)
    return bio.read()