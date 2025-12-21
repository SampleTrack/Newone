import logging
import asyncio
import re

from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait
from pyrogram.errors.exceptions.bad_request_400 import (
    ChannelInvalid,
    ChatAdminRequired,
    UsernameInvalid,
    UsernameNotModified
)
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from info import ADMINS
from info import INDEX_REQ_CHANNEL as LOG_CHANNEL
from database.ia_filterdb import save_file
from utils import temp

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

lock = asyncio.Lock()


# ========================= CALLBACK HANDLER ========================= #

@Client.on_callback_query(filters.regex(r"^index"))
async def index_callback(bot, query):
    if query.data == "index_cancel":
        temp.CANCEL = True
        return await query.answer("❌ Indexing cancelled")

    _, action, chat, last_msg_id, from_user = query.data.split("#")

    chat = int(chat) if chat.lstrip("-").isdigit() else chat
    last_msg_id = int(last_msg_id)
    from_user = int(from_user)

    if action == "reject":
        await query.message.delete()
        await bot.send_message(
            from_user,
            f"❌ Your indexing request for <code>{chat}</code> was rejected.",
            reply_to_message_id=last_msg_id
        )
        return

    if lock.locked():
        return await query.answer("⚠️ Another indexing process is running", show_alert=True)

    await query.answer("🚀 Starting indexing…", show_alert=True)

    msg = await query.message.edit(
        text="🚀 <b>Indexing Started</b>",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("❌ Cancel Indexing", callback_data="index_cancel")]]
        )
    )

    await index_files_to_db(last_msg_id, chat, msg, bot)


# ========================= MESSAGE HANDLER ========================= #

@Client.on_message(
    (filters.forwarded |
     (filters.regex(r"(https://)?(t\.me|telegram\.me|telegram\.dog)/(c/)?(\d+|[a-zA-Z0-9_]+)/(\d+)$")))
    & filters.text
    & filters.private
)
async def send_for_index(bot, message):

    # -------- Extract chat_id & last_msg_id -------- #
    if message.text:
        regex = re.compile(
            r"(https://)?(t\.me|telegram\.me|telegram\.dog)/(c/)?(\d+|[a-zA-Z0-9_]+)/(\d+)$"
        )
        match = regex.match(message.text)
        if not match:
            return await message.reply("❌ Invalid Telegram message link")

        chat_id = match.group(4)
        last_msg_id = int(match.group(5))

        if chat_id.isnumeric():
            chat_id = int("-100" + chat_id)

    elif message.forward_from_chat and message.forward_from_chat.type == enums.ChatType.CHANNEL:
        chat_id = message.forward_from_chat.username or message.forward_from_chat.id
        last_msg_id = message.forward_from_message_id

    else:
        return await message.reply("❌ Invalid message")

    # -------- Validate chat -------- #
    try:
        await bot.get_chat(chat_id)
    except ChannelInvalid:
        return await message.reply(
            "❌ This looks like a private channel.\n"
            "Make me admin there to index files."
        )
    except (UsernameInvalid, UsernameNotModified):
        return await message.reply("❌ Invalid link")
    except Exception as e:
        logger.exception(e)
        return await message.reply(f"❌ Error: {e}")

    # -------- Admin → Instant indexing -------- #
    if message.from_user.id in ADMINS:
        msg = await message.reply(
            text=(
                "🚀 <b>Indexing Started</b>\n\n"
                f"Chat: <code>{chat_id}</code>\n"
                f"From Message ID: <code>{last_msg_id}</code>\n\n"
                "Status: Processing messages…"
            ),
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("❌ Cancel Indexing", callback_data="index_cancel")]]
            )
        )

        await index_files_to_db(last_msg_id, chat_id, msg, bot)
        return

    # -------- Non-admin → Send request to LOG_CHANNEL -------- #
    try:
        if isinstance(chat_id, int):
            invite = (await bot.create_chat_invite_link(chat_id)).invite_link
        else:
            invite = f"@{chat_id}"
    except ChatAdminRequired:
        return await message.reply(
            "❌ I need admin permission to create invite link."
        )

    buttons = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Accept",
                    callback_data=f"index#accept#{chat_id}#{last_msg_id}#{message.from_user.id}"
                ),
                InlineKeyboardButton(
                    "❌ Reject",
                    callback_data=f"index#reject#{chat_id}#{last_msg_id}#{message.from_user.id}"
                )
            ]
        ]
    )

    await bot.send_message(
        LOG_CHANNEL,
        text=(
            "#IndexRequest\n\n"
            f"👤 User: {message.from_user.mention}\n"
            f"🆔 User ID: <code>{message.from_user.id}</code>\n"
            f"📌 Chat: <code>{chat_id}</code>\n"
            f"📨 From Msg ID: <code>{last_msg_id}</code>\n"
            f"🔗 Invite: {invite}"
        ),
        reply_markup=buttons
    )

    await message.reply("✅ Request sent to moderators. Please wait.")


# ========================= INDEX CORE ========================= #

@Client.on_message(filters.command("setskip") & filters.user(ADMINS))
async def set_skip(bot, message):
    if len(message.command) != 2:
        return await message.reply("Usage: /setskip <number>")

    try:
        temp.CURRENT = int(message.command[1])
        await message.reply(f"✅ Skip set to {temp.CURRENT}")
    except ValueError:
        await message.reply("❌ Skip must be an integer")


async def index_files_to_db(last_msg_id, chat, msg, bot):
    total = dup = err = deleted = no_media = unsupported = 0

    async with lock:
        temp.CANCEL = False
        current = temp.CURRENT

        try:
            async for m in bot.iter_messages(chat, last_msg_id, current):
                if temp.CANCEL:
                    break

                current += 1

                if current % 20 == 0:
                    await msg.edit_text(
                        text=(
                            f"📊 <b>Indexing Progress</b>\n\n"
                            f"Fetched: <code>{current}</code>\n"
                            f"Saved: <code>{total}</code>\n"
                            f"Duplicates: <code>{dup}</code>\n"
                            f"Deleted: <code>{deleted}</code>\n"
                            f"Non-Media: <code>{no_media + unsupported}</code>\n"
                            f"Errors: <code>{err}</code>"
                        ),
                        reply_markup=InlineKeyboardMarkup(
                            [[InlineKeyboardButton("❌ Cancel Indexing", callback_data="index_cancel")]]
                        )
                    )

                if m.empty:
                    deleted += 1
                    continue

                if not m.media:
                    no_media += 1
                    continue

                if m.media not in (
                    enums.MessageMediaType.VIDEO,
                    enums.MessageMediaType.AUDIO,
                    enums.MessageMediaType.DOCUMENT
                ):
                    unsupported += 1
                    continue

                media = getattr(m, m.media.value, None)
                if not media:
                    unsupported += 1
                    continue

                media.file_type = m.media.value
                media.caption = m.caption

                saved, code = await save_file(media)
                if saved:
                    total += 1
                elif code == 0:
                    dup += 1
                else:
                    err += 1

        except Exception as e:
            logger.exception(e)
            await msg.edit_text(f"❌ Error occurred:\n<code>{e}</code>")
            return

        await msg.edit_text(
            text=(
                "✅ <b>Indexing Completed</b>\n\n"
                f"Saved: <code>{total}</code>\n"
                f"Duplicates: <code>{dup}</code>\n"
                f"Deleted: <code>{deleted}</code>\n"
                f"Non-Media: <code>{no_media + unsupported}</code>\n"
                f"Errors: <code>{err}</code>"
            )
                )
