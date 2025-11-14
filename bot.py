import asyncio
import io
import os
import sqlite3
import time
import subprocess
import shutil
from datetime import datetime, timezone

from PIL import Image, ImageOps
from dotenv import load_dotenv

# cairosvg é opcional (para SVG). Se não existir, só mostramos aviso ao usar SVG.
try:
    import cairosvg
    CAIRO_OK = True
except Exception:
    CAIRO_OK = False

# OpenCV é opcional (para extrair o 1º frame de GIF/Animation MP4 do Telegram)
try:
    import cv2
    import numpy as np
    CV2_OK = True
except Exception:
    CV2_OK = False

from telegram import Update, InputFile
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    ChatMemberHandler,
)

# -------------------------
# Config & DB
# -------------------------
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
ALLOWED_CHAT_ID = int(os.getenv("ALLOWED_CHAT_ID", "0"))


from pathlib import Path


def _is_valid_ffmpeg(path_str: str) -> bool:
    if not path_str:
        return False
    p = Path(path_str.strip().strip('"').strip("'"))
    # se for um nome simples (ex.: "ffmpeg"), aceita
    if os.path.sep not in str(p) and os.path.altsep not in str(p):
        return shutil.which(str(p)) is not None
    return p.is_file()


# 0) Lê do .env e normaliza (remove aspas e converte barras)
FFMPEG_BIN = os.getenv("FFMPEG_BIN", "").strip().strip('"').strip("'")
FFMPEG_BIN = FFMPEG_BIN.replace("\\", "/")  # evita escapes do Windows


# 1) Se veio do .env mas NÃO existe, zera pra tentar fallbacks
if not _is_valid_ffmpeg(FFMPEG_BIN):
    FFMPEG_BIN = ""


# 2) PATH tradicional
if not FFMPEG_BIN:
    cand = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if cand:
        FFMPEG_BIN = cand


# 3) App Execution Alias (Windows) via PowerShell
if not FFMPEG_BIN and os.name == "nt":
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", "Get-Command ffmpeg | Select-Object -ExpandProperty Source"],
            text=True
        ).strip()
        if out and _is_valid_ffmpeg(out):
            FFMPEG_BIN = out
    except Exception:
        pass


# 4) Teste real: se 'ffmpeg -version' roda, usa literal "ffmpeg"
if not FFMPEG_BIN:
    try:
        subprocess.run(["ffmpeg", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        FFMPEG_BIN = "ffmpeg"
    except Exception:
        pass


print(f"FFmpeg detectado: {FFMPEG_BIN or 'NÃO ENCONTRADO'}")

if not BOT_TOKEN:
    raise RuntimeError("Defina BOT_TOKEN no .env")

def ensure_dir(path: str):
    try:
        os.makedirs(path, exist_ok=True)
    except FileExistsError:
        # Existe um ARQUIVO com esse nome: renomeia e cria a pasta
        if os.path.isfile(path):
            os.replace(path, path + ".bak")
            os.makedirs(path, exist_ok=True)
        else:
            raise
    return path

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = ensure_dir(os.path.join(BASE_DIR, "data"))
TMP_DIR  = ensure_dir(os.path.join(DATA_DIR, "stickers_tmp"))

DB_PATH = os.path.join(DATA_DIR, "groups.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS groups (
            chat_id INTEGER PRIMARY KEY,
            title TEXT,
            chat_type TEXT,
            joined_at TEXT
        )
    """)
    conn.commit()
    conn.close()

def upsert_group(chat_id: int, title: str, chat_type: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO groups (chat_id, title, chat_type, joined_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(chat_id) DO UPDATE SET
            title=excluded.title,
            chat_type=excluded.chat_type
    """, (chat_id, title or "", chat_type or "", datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()

def delete_group(chat_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM groups WHERE chat_id = ?", (chat_id,))
    conn.commit()
    conn.close()

def list_groups():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT chat_id, title, chat_type FROM groups ORDER BY title COLLATE NOCASE")
    rows = cur.fetchall()
    conn.close()
    return rows

# -------------------------
# Helpers
# -------------------------

SUPPORTED_MIME = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/svg+xml",
    "image/tiff",
    "image/bmp",
    "video/mp4",
    "video/webm",
    "video/quicktime",     # .mov
    "video/x-matroska",    # .mkv
}

SUPPORTED_EXT = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".tif", ".tiff", ".bmp",
    ".mp4", ".webm", ".mov", ".mkv", ".avi"
}

def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID

def is_allowed_chat(chat_id: int) -> bool:
    # O bot só responde no grupo permitido (ALLOWED_CHAT_ID).
    return chat_id == ALLOWED_CHAT_ID

async def reply_only_in_allowed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Retorna True se pode responder; caso contrário, ignora."""
    chat = update.effective_chat
    if not chat:
        return False
    if is_allowed_chat(chat.id):
        return True
    # Silenciosamente ignora fora do grupo permitido
    return False

def pil_from_svg_bytes(svg_bytes: bytes) -> Image.Image:
    """Converte SVG -> PNG em memória e abre no Pillow."""
    if not CAIRO_OK:
        raise RuntimeError("SVG não habilitado. Instale cairosvg.")
    png_bytes = cairosvg.svg2png(bytestring=svg_bytes)
    return Image.open(io.BytesIO(png_bytes)).convert("RGBA")

def fit_to_sticker_canvas(img: Image.Image, size: int = 512) -> Image.Image:
    """Redimensiona mantendo proporção e centraliza em canvas 512x512 transparente."""
    img = img.convert("RGBA")
    # Limita o maior lado para 'size'
    img.thumbnail((size, size), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    x = (size - img.width) // 2
    y = (size - img.height) // 2
    canvas.paste(img, (x, y), img)
    return canvas

def convert_to_sticker_webp(input_bytes: bytes, mime_type: str, filename: str | None = None) -> bytes:
    """Converte bytes de imagem (ou 1º frame de vídeo/animation) em WebP 512x512."""
    mime_type = (mime_type or "").lower()
    ext = (os.path.splitext(filename or "")[1] or "").lower()

    # 1) SVG
    if mime_type == "image/svg+xml" or ext == ".svg":
        img = pil_from_svg_bytes(input_bytes)

    # 2) Vídeo (Telegram Animation: geralmente video/mp4)
    elif mime_type.startswith("video/") or ext in {".mp4", ".mov", ".mkv", ".webm"}:
        if not CV2_OK:
            raise RuntimeError("eu recebi uma animação (mp4), mas o suporte a vídeo nao ta habilitado. dica: instale opencv-python")
        # grava em arquivo temporário e captura 1º frame
        tmp_in = os.path.join(TMP_DIR, f"anim_{int(time.time()*1000)}{ext or '.mp4'}")
        with open(tmp_in, "wb") as f:
            f.write(input_bytes)
        cap = cv2.VideoCapture(tmp_in)
        ok, frame = cap.read()
        cap.release()
        try:
            os.remove(tmp_in)
        except Exception:
            pass
        if not ok or frame is None:
            raise RuntimeError("eu nao consegui ler o primeiro frame da animação")
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGBA)
        img = Image.fromarray(frame)

    # 3) Imagens comuns (inclui GIF clássico como imagem)
    else:
        img = Image.open(io.BytesIO(input_bytes))
        try:
            if getattr(img, "is_animated", False):
                img.seek(0)  # primeiro frame de GIF real
        except Exception:
            pass
        img = img.convert("RGBA")

    sticker_img = fit_to_sticker_canvas(img, 512)
    out = io.BytesIO()
    # WebP estático
    sticker_img.save(out, format="WEBP", method=6, quality=95)
    out.seek(0)
    return out.read()

def convert_to_animated_sticker_webm(
    input_bytes: bytes,
    mime_type: str,
    filename: str | None = None,
    *,
    max_seconds: int = 3,
    max_size: int = 512,
    fps: int = 30,
    bitrate: str = "300k"
) -> bytes:
    """
    Converte GIF/MP4/WebM em figurinha animada (video sticker .webm VP9) 512x512, sem áudio, ~3s.
    Requer FFmpeg no PATH. Se não houver, levanta erro amigável.
    """
    if not FFMPEG_BIN:
        raise RuntimeError("FFmpeg não encontrado. Instale (winget/choco/scoop) ou defina FFMPEG_BIN no .env apontando para o ffmpeg.exe.")

    mime_type = (mime_type or "").lower()
    ext = (os.path.splitext(filename or "")[1] or "").lower()
    if not ext:
        ext = ".mp4" if mime_type.startswith("video/") else (".gif" if mime_type == "image/gif" else ".mp4")

    # grava entrada temporária
    ts = int(time.time() * 1000)
    in_path  = os.path.join(TMP_DIR, f"in_{ts}{ext}")
    out_path = os.path.join(TMP_DIR, f"sticker_{ts}.webm")
    with open(in_path, "wb") as f:
        f.write(input_bytes)

    # Filtro: escala mantendo proporção e preenche para 512x512 com fundo transparente
    vf = f"scale={max_size}:{max_size}:force_original_aspect_ratio=decrease:flags=lanczos," \
         f"pad={max_size}:{max_size}:(ow-iw)/2:(oh-ih)/2:color=0x00000000,fps={fps}"

    ff = FFMPEG_BIN.replace("\\", "/")
    inp = in_path.replace("\\", "/")
    outp = out_path.replace("\\", "/")


    cmd = [
        ff, "-y", "-hide_banner", "-loglevel", "error",
        "-i", inp,
        "-t", str(max_seconds),
        "-an",
        "-vf", vf,
        "-c:v", "libvpx-vp9",
        "-pix_fmt", "yuva420p",
        "-b:v", bitrate,
        outp
    ]

    try:
        subprocess.run(cmd, check=True)
        with open(out_path, "rb") as f:
            data = f.read()
        return data
    finally:
        # limpa temporários silenciosamente
        for p in (in_path, out_path):
            try:
                if os.path.exists(p) and p.endswith(".webm") is False:  # mantém o .webm se falhar antes de ler
                    os.remove(p)
            except Exception:
                pass

async def extract_media_bytes_and_meta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Procura imagem no comando (/fig) priorizando:
     1) mensagem respondida (reply)
     2) a própria mensagem do comando (se vier com arquivo)
    Retorna (bytes, mime_type, filename) ou (None, None, None)
    """
    bot = context.bot
    msg = update.effective_message

    candidates = []
    # 1) Se estiver respondendo alguém
    if msg and msg.reply_to_message:
        candidates.append(msg.reply_to_message)
    # 2) A própria mensagem
    candidates.append(msg)

    for m in candidates:
        # Photo (JPEG compactado)
        if m.photo:
            photo = m.photo[-1]  # maior resolução
            file = await bot.get_file(photo.file_id)
            data = await file.download_as_bytearray()
            return bytes(data), "image/jpeg", "photo.jpg"

        # Documento com imagem
        if m.document and (m.document.mime_type in SUPPORTED_MIME or (m.document.file_name and os.path.splitext(m.document.file_name)[1].lower() in SUPPORTED_EXT)):
            file = await bot.get_file(m.document.file_id)
            data = await file.download_as_bytearray()
            mime = m.document.mime_type or ""
            name = m.document.file_name or "image"
            return bytes(data), mime, name

        # GIF (Telegram Animation) costuma vir como MP4 (video/mp4)
        if m.animation:
            file = await bot.get_file(m.animation.file_id)
            data = await file.download_as_bytearray()
            mime = getattr(m.animation, "mime_type", None) or "video/mp4"
            name = getattr(m.animation, "file_name", None) or "animation.mp4"
            return bytes(data), mime, name

    return None, None, None

# -------------------------
# Handlers
# -------------------------

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Opcional: só responde no grupo permitido
    if not await reply_only_in_allowed(update, context):
        return
    await update.effective_message.reply_text("Faaala! Tudo bem? Bot das figurinhas do Dinastia na área. use /fig respondendo a uma imagem")

async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await reply_only_in_allowed(update, context):
        return
    t0 = time.perf_counter()
    # Faz algo simples para medir o processamento
    await asyncio.sleep(0)
    dt_ms = (time.perf_counter() - t0) * 1000
    # Latência de entrega (servidor Telegram até aqui)
    msg_dt = update.effective_message.date  # UTC
    now_utc = datetime.now(timezone.utc)
    delivery_ms = (now_utc - msg_dt).total_seconds() * 1000
    text = f"🏓 Pong!\n• processamento: {dt_ms:.1f} ms\n• entrega: {delivery_ms:.0f} ms"
    await update.effective_message.reply_text(text)

async def fig_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await reply_only_in_allowed(update, context):
        return

    # Pega mídia (reply ou própria msg)
    data, mime, name = await extract_media_bytes_and_meta(update, context)
    if not data:
        await update.effective_message.reply_text(
            "use /fig respondendo a uma imagem, arquivo ou gif."
        )
        return

    # Se for SVG sem cairosvg
    if (mime or "").lower() == "image/svg+xml" and not CAIRO_OK:
        await update.effective_message.reply_text(
            "recebi um SVG, mas a conversão de SVG está desabilitada. dica: instala `cairosvg` pra ativar"
        )
        return

    ext = (os.path.splitext(name or "")[1] or "").lower()
    is_video_like = (mime or "").lower().startswith("video/") or ext in {".mp4", ".mov", ".mkv", ".webm"}
    is_gif = (mime or "").lower() == "image/gif" or ext == ".gif"

    try:
        if is_video_like or is_gif:
            # Figurinha ANIMADA .webm (video sticker)
            sticker_bytes = convert_to_animated_sticker_webm(data, mime, name)
            bio = io.BytesIO(sticker_bytes)
            bio.name = "sticker.webm"
        else:
            # Figurinha estática .webp
            sticker_bytes = convert_to_sticker_webp(data, mime, name)
            bio = io.BytesIO(sticker_bytes)
            bio.name = "sticker.webp"
    except Exception as e:
        await update.effective_message.reply_text(f"eu não consegui converter essa imagem em fig. motivo: {e}")
        return

    # Envia figurinha respondendo ao comando do membro
    try:
       await update.effective_message.reply_sticker(sticker=InputFile(bio))
    except Exception as e:
        # Se falhar como sticker, tenta enviar como foto (fallback raro)
        try:
            bio.seek(0)
            await update.effective_message.reply_photo(photo=InputFile(bio), caption="enviei como imagem pq o telegram ñ permitiu a conversão")
        except Exception:
            await update.effective_message.reply_text(f"falhei ao tentar enviar a sua figurinha: {e}")

async def vergrupos_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_owner(user.id):
        return  # ignora silenciosamente

    rows = list_groups()
    if not rows:
        await update.effective_message.reply_text("ainda não estou em nenhum grupo.")
        return

    parts = [f"Quantidade: {len(rows)}"]
    for chat_id, title, chat_type in rows:
        parts.append(f"\nNome: {title or '(sem título)'}\nID: {chat_id}\nTipo: {chat_type}")
    await update.effective_message.reply_text("\n".join(parts))

async def sair_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_owner(user.id):
        return  # ignora silenciosamente

    args = context.args or []
    if not args:
        await update.effective_message.reply_text("Uso: /sair id_do_grupo")
        return

    try:
        target_id = int(args[0])
    except ValueError:
        await update.effective_message.reply_text("ID inválido. Use apenas números (ex.: -1001234567890).")
        return

    try:
        ok = await context.bot.leave_chat(target_id)
        if ok:
            delete_group(target_id)
            await update.effective_message.reply_text(f"Saí do grupo {target_id}.")
        else:
            await update.effective_message.reply_text(f"Não consegui sair do grupo {target_id}.")
    except Exception as e:
        await update.effective_message.reply_text(f"Erro ao sair do grupo {target_id}: {e}")

async def my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Atualiza DB quando o bot entra/sai de grupos."""
    chat = update.effective_chat
    if not chat:
        return
    new_status = update.my_chat_member.new_chat_member.status
    title = chat.title or (chat.username or "")
    chat_type = chat.type or ""

    if new_status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR):
        upsert_group(chat.id, title, chat_type)
    elif new_status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
        delete_group(chat.id)

def main():
    init_db()
    app: Application = ApplicationBuilder().token(BOT_TOKEN).build()

    # Handlers de comandos
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("ping", ping_cmd))
    app.add_handler(CommandHandler("fig", fig_cmd))
    app.add_handler(CommandHandler("vergrupos", vergrupos_cmd))
    app.add_handler(CommandHandler("sair", sair_cmd))

    # Handler para ser informado quando entra/sai de grupos
    app.add_handler(ChatMemberHandler(my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))

    print("Bot rodando... Ctrl+C para parar.")
    # você já importa Update lá em cima: from telegram import Update, InputFile
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()