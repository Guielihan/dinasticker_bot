from PIL import Image, ImageDraw, ImageFont
import io
from pathlib import Path 

# pasta onde ficarão as fontes extras
FONT_DIR = Path(__file__).resolve().parent / "fonts"

# tenta carregar uma fonte decente; ajuste o caminho se quiser uma ttf no repo
def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """
    Tenta várias fontes, primeiro na pasta ./fonts, depois no sistema.
    Prioridade: Noto / Symbola / DejaVu / Arial.
    """
    candidates = [
        
        "NotoSansCherokee-Regular.ttf",   
        "NotoSansMath-Regular.ttf",
        "NotoSansSymbols2-Regular.ttf",
        "NotoEmoji-Regular.ttf",
        "NotoSans-Regular.ttf",
        "Symbola.ttf",                    
        "Quicksand-Regular.ttf",
        "Oswald-Regular.ttf",
        "Lora-Regular.ttf",
        "Merriweather_120pt-Regular.ttf",
        "Ponnala-Regular.ttf",
        "seguiemj.ttf",        
        "DejaVuSans.ttf",
        "DejaVuSansMono.ttf",
        "DejaVuSansCondensed.ttf",
        "Arial.ttf",
        "arial.ttf",
    ]

    for name in candidates:
        font_path = FONT_DIR / name
        try:
            return ImageFont.truetype(str(font_path), size)
        except Exception:
            pass

        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue

    return ImageFont.load_default()

def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_w: int
):
    """
    Quebra o texto em várias linhas:
    - tenta primeiro por palavras (normal);
    - se uma "palavra" sozinha estourar max_w (ex.: ".............." gigante),
      ela é quebrada em pedaços menores para caber na bolha.
    """
    text = text.replace("\r", "")
    words = text.split(" ")
    lines: list[str] = []
    cur = ""

    def _width(s: str) -> int:
        if not s:
            return 0
        try:
            bbox = draw.textbbox((0, 0), s, font=font)
            return bbox[2] - bbox[0]
        except AttributeError:
            return draw.textsize(s, font=font)[0]

    for w in words:
        if w == "":
            test = (cur + " ").rstrip()
            if _width(test) <= max_w or not cur:
                cur = test
            else:
                if cur:
                    lines.append(cur)
                cur = ""
            continue

        w_px = _width(w)

        if w_px > max_w:
            if cur:
                lines.append(cur)
                cur = ""
            tmp = ""
            for ch in w:
                test = tmp + ch
                if _width(test) <= max_w or not tmp:
                    tmp = test
                else:
                    lines.append(tmp)
                    tmp = ch
            if tmp:
                cur = tmp
        else:
            test = (cur + " " + w).strip()
            if _width(test) <= max_w or not cur:
                cur = test
            else:
                lines.append(cur)
                cur = w

    if cur:
        lines.append(cur)

    if not lines:
        lines = [""]

    return lines

def _measure(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont):
    """ mede largura/altura de uma string respeitando Pillow novo/antigo """
    if not text:
        return 0, 0
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0], bbox[3] - bbox[1]
    except AttributeError:
        return draw.textsize(text, font=font)

def _circle_avatar(avatar_img: Image.Image, size: int) -> Image.Image:
    av = avatar_img.convert("RGBA").resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    d.ellipse((0, 0, size, size), fill=255)
    av.putalpha(mask)
    return av

def _resize_badge(badge_img: Image.Image, size: int) -> Image.Image:
    b = badge_img.convert("RGBA")
    b.thumbnail((size, size), Image.LANCZOS)
    return b

def _initials_avatar(initials: str, size: int) -> Image.Image:
    """
    Gera um avatar redondo com as iniciais (estilo padrão do Telegram).
    Usado quando o usuário não tem foto de perfil na API.
    """
    initials = (initials or "").strip().upper()[:2] or "?"
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    circle_color = (44, 28, 74, 255)
    draw.ellipse((0, 0, size, size), fill=circle_color)

    # texto centralizado
    font = _load_font(int(size * 0.45))
    w, h = _measure(draw, initials, font)
    x = (size - w) // 2
    y = (size - h) // 2 - 2
    draw.text((x, y), initials, font=font, fill=(245, 246, 248, 255))

    return img

def make_quote_sticker(
    text: str,
    author_name: str | None = None,
    avatar_img: Image.Image | None = None,
    badge_img: Image.Image | None = None,
    *,
    theme: str = "dark",
    bg_hex: str | None = None, # cor da bolha
    txt_hex: str | None = None,# cor do texto
    show_avatar: bool = True,
    canvas_size: int = 512,
) -> bytes:
    # canvas transparente 512x512
    W = H = canvas_size
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(out)

    # cores básicas
    if theme == "light":
        bubble = (245, 246, 248, 255)
        fg = (25, 25, 28, 255)
        meta = (90, 90, 95, 255)
    else:
        bubble = (44, 28, 74, 255)          # balão padrão roxo escuro
        fg = (245, 246, 248, 255)           # texto branco
        meta = (180, 190, 205, 255)         # nome mais clarinho

    if bg_hex:
        bg_hex = bg_hex.lstrip("#")
        if len(bg_hex) == 6:
            r = int(bg_hex[0:2], 16)
            g = int(bg_hex[2:4], 16)
            b = int(bg_hex[4:6], 16)
            bubble = (r, g, b, 255)

            # escolhe automaticamente a cor do texto de acordo com o brilho do fundo
            luminancia = 0.299 * r + 0.587 * g + 0.114 * b
            if luminancia > 140:
                fg = (25, 25, 28, 255)
                meta = (70, 70, 80, 255)
            else:
                fg = (245, 246, 248, 255)
                meta = (190, 195, 210, 255)

    if txt_hex:
        txt_hex = txt_hex.lstrip("#")
        if len(txt_hex) == 6:
            fg = tuple(int(txt_hex[i:i+2], 16) for i in (0, 2, 4)) + (255,)

    P = 6                              
    AV = 140 if show_avatar else 0    
    GAP = 24 if AV else 0
    INNER_X = 22                    
    INNER_Y = 18                         

    font_name = _load_font(28)
    font_text = _load_font(34)

    max_text_w = W - 2 * P - AV - GAP - 2 * INNER_X

    lines = _wrap_text(draw, text, font_text, max_text_w)

    line_h = font_text.getbbox("Ag")[3]
    text_width_max = 0
    for ln in lines:
        w, _ = _measure(draw, ln, font_text)
        if w > text_width_max:
            text_width_max = w

    name_w = 0
    name_h = 0
    name_text_h = 0
    if author_name:
        name_w, name_text_h = _measure(draw, author_name, font_name)
        name_h = name_text_h + 6 

        if badge_img is not None:
            badge_extra = font_name.size + 4  
            name_w += badge_extra + 8       

    content_w = max(text_width_max, name_w)

    text_block_h = 0
    if lines:
        text_block_h = len(lines) * line_h + (len(lines) - 1) * 8

    box_h = max(AV, INNER_Y + name_h + text_block_h + INNER_Y)

    bubble_w = content_w + 2 * INNER_X
    max_bubble_w = W - 2 * P - AV - GAP
    if bubble_w > max_bubble_w:
        bubble_w = max_bubble_w

    group_w = AV + (GAP if AV else 0) + bubble_w

    group_x0 = (W - group_w) // 2
    y0 = (H - box_h) // 2
    x_avatar = group_x0
    x0 = x_avatar + (AV + (GAP if AV else 0)) 
    x1 = x0 + bubble_w
    y1 = y0 + box_h

    radius = 26
    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=bubble)

    cur_x = x0 + INNER_X
    cur_y = y0 + INNER_Y

    if AV:
        if avatar_img is not None:
            av = _circle_avatar(avatar_img, AV)
        else:
            initials = ""
            if author_name:
                parts = [p for p in author_name.split() if p.strip()]
                if parts:
                    initials = (parts[0][0] + (parts[1][0] if len(parts) > 1 else "")).upper()
            av = _initials_avatar(initials, AV)

        av_y = y0 + (box_h - AV) // 2
        out.alpha_composite(av, (x_avatar, av_y))

    if author_name:
        # desenha o nome
        draw.text((cur_x, cur_y), author_name, font=font_name, fill=meta)

        if badge_img is not None:
            badge_size = font_name.size + 4
            b = _resize_badge(badge_img, badge_size)

            name_w_no_pad, name_text_h = _measure(draw, author_name, font=font_name)
            badge_x = cur_x + name_w_no_pad + 8
            badge_y = cur_y + (name_text_h - b.height) // 2
            out.alpha_composite(b, (int(badge_x), int(badge_y)))

        cur_y += name_h

    for i, ln in enumerate(lines):
        draw.text(
            (cur_x, cur_y + i * (line_h + 8)),
            ln,
            font=font_text,
            fill=fg
        )

    bio = io.BytesIO()
    out.save(bio, "WEBP", quality=95, method=6)
    bio.seek(0)
    return bio.read()