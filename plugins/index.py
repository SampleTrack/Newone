import logging
import re
import asyncio

from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait
from pyrogram.errors.exceptions.bad_request_400 import (
    ChannelInvalid,
    ChatAdminRequired,
    UsernameInvalid,
    UsernameNotModified
)
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from info import CHANNELS, LOG_CHANNEL, ADMINS
from database.ia_filterdb import save_file
from utils import temp


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

lock = asyncio.Lock()

LINK_REGEX = r"(?:https?://)?t\.me/(?:c/)?([A-Za-z0-9_]+)/(\d+)"


# -------------------- MEDIA AUTO SAVE --------------------

@Client.on_message(filters.chat(CHANNELS) & (filters.document | filters.video | filters.audio))
async def media(bot, message):
    for file_type in ("document", "video", "audio"):
        media = getattr(message, file_type, None)
        if media:
            media.file_type = file_type
            media.caption = message.caption
            await save_file(media)
            return


# -------------------- LINK ONLY INDEX REQUEST --------------------

@Client.on_message(
    filters.private
    & filters.text
    & filters.user(ADMINS)
    & filters.regex(LINK_REGEX)
)
async def send_for_index(bot, message):
    match = re.search(LINK_REGEX, message.text)
    if not match:
        return await message.reply("Invalid Telegram message link.")

    chat_id = match.group(1)
    last_msg_id = int(match.group(2))

    if chat_id.isnumeric():
        chat_id = int("-100" + chat_id)

    try:
        await bot.get_chat(chat_id)
    except ChannelInvalid:
        return await message.reply("Private channel or group. Add me as admin.")
    except (UsernameInvalid, UsernameNotModified):
        return await message.reply("Invalid channel username.")
    except ChatAdminRequired:
        return await message.reply("I must be admin to index messages.")
    except Exception as e:
        return await message.reply(f"Error: {e}")

    try:
        msg = await bot.get_messages(chat_id, last_msg_id)
        if msg.empty:
            return await message.reply("Message not found or access denied.")
    except Exception:
        return await message.reply("Cannot access message. Check admin rights.")

    buttons = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Yes, Index", callback_data=f"index#{chat_id}#{last_msg_id}")],
            [InlineKeyboardButton("Close", callback_data="close_data")]
        ]
    )

    await message.reply(
        text=(
            "Do you want to start indexing?\n\n"
            f"Chat: `{chat_id}`\n"
            f"Last Message ID: `{last_msg_id}`"
        ),
        reply_markup=buttons
    )


# -------------------- CALLBACK HANDLER --------------------

@Client.on_callback_query(filters.regex(r"^index"))
async def index_files(bot, query):
    if query.data == "index_cancel":
        temp.CANCEL = True
        return await query.answer("Indexing cancelled.", show_alert=True)

    _, chat, last_msg_id = query.data.split("#")

    if lock.locked():
        return await query.answer("Another indexing process is running.", show_alert=True)

    msg = query.message

    cancel_btn = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Cancel", callback_data="index_cancel")]]
    )

    await msg.edit_text("Indexing started...", reply_markup=cancel_btn)

    try:
        chat = int(chat)
    except ValueError:
        pass

    await index_files_to_db(int(last_msg_id), chat, msg, bot)
    
