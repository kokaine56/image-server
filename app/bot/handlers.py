import datetime
import secrets
import logging
from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command
from sqlalchemy import select, func

from app.config import STORAGE_CHANNEL_ID, DOMAIN, USER_LIMIT
from app.database import async_session
from app.models import Image, BannedUser
from app.services.moderation_service import moderation_service
from app.services.image_service import image_service
from app.bot.bot_instance import bot

logger = logging.getLogger(__name__)
router = Router()

async def generate_unique_slug(db) -> str:
    while True:
        # Generate 6 character alphanumeric slug
        slug = secrets.token_urlsafe(5).replace("-", "").replace("_", "")[:6].lower()
        stmt = select(Image).where(Image.slug == slug)
        res = await db.execute(stmt)
        if not res.scalar():
            return slug

async def is_user_banned(db, user_id: int) -> bool:
    stmt = select(BannedUser).where(BannedUser.telegram_id == user_id)
    res = await db.execute(stmt)
    return res.scalar() is not None

async def check_user_limit(db, user_id: int) -> bool:
    day_ago = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
    stmt = select(func.count(Image.id)).where(Image.uploaded_by == user_id, Image.created_at >= day_ago)
    count = (await db.execute(stmt)).scalar() or 0
    return count < USER_LIMIT

@router.message(Command("start"))
async def start_command(message: Message):
    welcome_text = (
        "📸 <b>Welcome to the Premium Image Hosting Bot!</b>\n\n"
        "Send me any image (as a photo or document), and I will host it securely on our Telegram-backed database storage!\n\n"
        "<b>Commands:</b>\n"
        "/help - How to use the bot\n"
        "/stats - Quick bot storage metrics\n"
        "/myuploads - View your hosted uploads"
    )
    await message.reply(welcome_text)

@router.message(Command("help"))
async def help_command(message: Message):
    help_text = (
        "📖 <b>How to use the Image Hosting Bot:</b>\n\n"
        "1. Send any photo or image file directly to the bot.\n"
        "2. The bot will validate the file type (JPEG, PNG, WEBP, GIF supported).\n"
        "3. It checks the content for safety and stores it in our secure channel storage.\n"
        "4. You will receive a direct preview link, a raw image URL, and a deletion token.\n\n"
        "<b>Upload Limits:</b>\n"
        f"- Up to {USER_LIMIT} uploads per 24 hours per Telegram user."
    )
    await message.reply(help_text)

@router.message(Command("stats"))
async def stats_command(message: Message):
    async with async_session() as db:
        uploads_res = await db.execute(select(func.count(Image.id)))
        total_uploads = uploads_res.scalar() or 0

        views_res = await db.execute(select(func.sum(Image.views)))
        total_views = views_res.scalar() or 0

        storage_res = await db.execute(select(func.sum(Image.file_size)))
        total_bytes = storage_res.scalar() or 0
        total_storage_mb = round(total_bytes / (1024 * 1024), 2)

    stats_text = (
        "📊 <b>Bot Storage Statistics:</b>\n\n"
        f"📁 <b>Total Images Hosted:</b> {total_uploads}\n"
        f"👁️ <b>Total Image Views:</b> {total_views}\n"
        f"💾 <b>Total Storage footprint:</b> {total_storage_mb} MB"
    )
    await message.reply(stats_text)

@router.message(Command("myuploads"))
async def myuploads_command(message: Message):
    user_id = message.from_user.id
    async with async_session() as db:
        stmt = select(Image).where(Image.uploaded_by == user_id).order_by(Image.created_at.desc()).limit(10)
        res = await db.execute(stmt)
        images = res.scalars().all()

    if not images:
        await message.reply("📂 You haven't uploaded any images yet!")
        return

    uploads_text = "📂 <b>Your Last 10 Uploads:</b>\n\n"
    for idx, img in enumerate(images, 1):
        uploads_text += f"{idx}. <code>{img.slug}</code> - <a href='{DOMAIN}/i/{img.slug}'>View Image</a> ({img.views} views)\n"

    await message.reply(uploads_text, disable_web_page_preview=True)

@router.message(F.photo | F.document)
async def media_upload_handler(message: Message):
    user_id = message.from_user.id
    
    async with async_session() as db:
        # 1. Ban Check
        if await is_user_banned(db, user_id):
            await message.reply("❌ You are banned from using this service.")
            return

        # 2. Limit Check
        if not await check_user_limit(db, user_id):
            await message.reply("❌ Daily upload limit reached. Try again in 24 hours.")
            return

        # 3. Identify Media & Extract Info
        file_id = None
        file_unique_id = None
        mime_type = "image/jpeg"
        file_size = 0
        file_name = "image.jpg"

        if message.photo:
            photo = message.photo[-1]
            file_id = photo.file_id
            file_unique_id = photo.file_unique_id
            file_size = photo.file_size
            mime_type = "image/jpeg"
        elif message.document:
            doc = message.document
            # Validate mime type
            if not doc.mime_type or not doc.mime_type.startswith("image/"):
                await message.reply("❌ Only image files (JPEG, PNG, WEBP, GIF) are allowed!")
                return
            file_id = doc.file_id
            file_unique_id = doc.file_unique_id
            file_size = doc.file_size
            mime_type = doc.mime_type
            file_name = doc.file_name or "image.jpg"

        if not file_id:
            await message.reply("❌ Failed to process upload attachment.")
            return

        status_msg = await message.reply("⏳ <i>Processing image and database entry...</i>")

        try:
            # 4. Download and validate content
            tg_file = await bot.get_file(file_id)
            if not tg_file.file_path:
                raise ValueError("Telegram file path missing")

            # Fetch file contents in-memory
            file_bytes = bytes()
            file_stream = await bot.download_file(tg_file.file_path)
            if file_stream:
                file_bytes = file_stream.read()

            # Ensure valid image with Pillow
            try:
                optimized_bytes = image_service.validate_and_optimize(file_bytes)
                file_size = len(optimized_bytes)
            except ValueError:
                await status_msg.edit_text("❌ Upload validation failed. File is not a valid or supported image format.")
                return

            # 5. Content Moderation / NSFW Check
            is_nsfw = moderation_service.check_nsfw(optimized_bytes)

            # 6. Copy message to database channel storage
            # Aiogram 3 copy_message
            channel_msg = await bot.copy_message(
                chat_id=STORAGE_CHANNEL_ID,
                from_chat_id=message.chat.id,
                message_id=message.message_id
            )

            # 7. Write Database Record
            slug = await generate_unique_slug(db)
            delete_token = secrets.token_hex(16)

            new_image = Image(
                slug=slug,
                file_id=file_id,
                file_unique_id=file_unique_id,
                message_id=channel_msg.message_id,
                mime_type=mime_type,
                file_size=file_size,
                uploaded_by=user_id,
                delete_token=delete_token,
                is_nsfw=is_nsfw,
                nsfw_checked=True
            )
            db.add(new_image)
            await db.commit()

            # 8. Send URLs Response
            view_url = f"{DOMAIN}/i/{slug}"
            raw_url = f"{DOMAIN}/raw/{slug}"
            delete_url = f"{DOMAIN}/delete/{slug}/{delete_token}"

            response_text = (
                "✅ <b>Image uploaded successfully!</b>\n\n"
                f"👁️ <b>View:</b> {view_url}\n"
                f"🔗 <b>Direct link:</b> {raw_url}\n"
                f"🗑️ <b>Delete link:</b> {delete_url}\n"
            )
            if is_nsfw:
                response_text += "\n⚠️ <i>Note: This upload has been flagged for NSFW review.</i>"

            await status_msg.edit_text(response_text, disable_web_page_preview=True)

        except Exception as e:
            logger.error(f"Error handling media upload: {e}")
            await status_msg.edit_text("❌ System error processing your upload. Please try again later.")
