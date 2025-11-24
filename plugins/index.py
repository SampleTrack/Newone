import logging
import asyncio
import re
from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait, ChannelInvalid, ChatAdminRequired, UsernameInvalid, UsernameNotModified
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# Local imports (Assumed these exist based on your snippet)
from info import ADMINS, INDEX_REQ_CHANNEL as LOG_CHANNEL
from database.ia_filterdb import save_file
from utils import temp

# Configuration
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
lock = asyncio.Lock()

@Client.on_callback_query(filters.regex(r'^index'))
async def index_files(bot, query):
    """
    Callback handler to accept or reject indexing requests.
    """
    if query.data.startswith('index_cancel'):
        temp.CANCEL = True
        return await query.answer("Cancelling Indexing...", show_alert=True)

    # Decode payload: action#chat_id#last_msg_id#from_user_id
    try:
        _, action, chat_id, last_msg_id, user_id = query.data.split("#")
    except ValueError:
        return await query.answer("Invalid Data", show_alert=True)

    if action == 'reject':
        await query.message.delete()
        await bot.send_message(
            int(user_id),
            f"❌ **Request Declined**\n\nYour submission for indexing {chat_id} has been declined by our moderators.",
            reply_to_message_id=int(last_msg_id)
        )
        return

    # Check if a process is already running
    if lock.locked():
        return await query.answer('⚠️ Another process is currently running. Please wait.', show_alert=True)

    msg = query.message
    await query.answer('Processing... ⏳', show_alert=False)

    # Notify the user if they are not an admin
    if int(user_id) not in ADMINS:
        await bot.send_message(
            int(user_id),
            f"✅ **Request Accepted**\n\nYour submission for indexing {chat_id} has been approved and will be processed shortly.",
            reply_to_message_id=int(last_msg_id)
        )

    await msg.edit(
        "⏳ **Indexing Started...**",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton('🚫 Cancel Indexing', callback_data='index_cancel')]]
        )
    )

    # Convert chat_id to int if possible (for channel IDs)
    try:
        chat_id = int(chat_id)
    except ValueError:
        pass  # It's a username string

    # Start the indexing process inside the lock
    async with lock:
        await index_files_to_db(int(last_msg_id), chat_id, msg, bot)


@Client.on_message((filters.forwarded | (filters.regex(r"(https://)?(t\.me/|telegram\.me/|telegram\.dog/)(c/)?(\d+|[a-zA-Z_0-9]+)/(\d+)$")) & filters.text) & filters.private & filters.incoming)
async def send_for_index(bot, message):
    """
    Handler for users sending links or forwarding messages to request indexing.
    """
    chat_id = None
    last_msg_id = 0

    # 1. Parse from Text Link
    if message.text:
        regex = re.compile(r"(https://)?(t\.me/|telegram\.me/|telegram\.dog/)(c/)?(\d+|[a-zA-Z_0-9]+)/(\d+)$")
        match = regex.match(message.text)
        if not match:
            return await message.reply('⚠️ **Invalid Link**\nPlease provide a valid Telegram post link.')
        
        chat_identifier = match.group(4)
        last_msg_id = int(match.group(5))
        
        if chat_identifier.isdigit():
            chat_id = int(f"-100{chat_identifier}")
        else:
            chat_id = chat_identifier

    # 2. Parse from Forwarded Message
    elif message.forward_from_chat and message.forward_from_chat.type == enums.ChatType.CHANNEL:
        last_msg_id = message.forward_from_message_id
        chat_id = message.forward_from_chat.username or message.forward_from_chat.id

    else:
        return

    # 3. Validate Bot Permissions
    try:
        await bot.get_chat(chat_id)
    except ChannelInvalid:
        return await message.reply('❌ **Access Denied**\nThis appears to be a private channel or group. Please make me an Admin there first.')
    except (UsernameInvalid, UsernameNotModified):
        return await message.reply('❌ **Invalid Link**\nThe provided link or username is invalid.')
    except Exception as e:
        logger.exception(e)
        return await message.reply(f'⚠️ **Error Occurred:**\n`{e}`')

    # 4. Verify Message Existence
    try:
        k = await bot.get_messages(chat_id, last_msg_id)
    except Exception:
        return await message.reply('❌ **Access Error**\nEnsure I am an Admin in the channel if it is private.')
    
    if not k or k.empty:
        return await message.reply('⚠️ **Message Not Found**\nI cannot access this message. Ensure I am an Admin.')

    # 5. Handle Admin Direct Indexing
    if message.from_user.id in ADMINS:
        buttons = [
            [
                InlineKeyboardButton('✅ Yes, Index',
                                     callback_data=f'index#accept#{chat_id}#{last_msg_id}#{message.from_user.id}')
            ],
            [
                InlineKeyboardButton('❌ Close', callback_data='close_data'),
            ]
        ]
        return await message.reply(
            f'**❓ Index Confirmation**\n\n'
            f'Do you want to index this Channel/Group?\n'
            f'**ID/User:** `{chat_id}`\n'
            f'**Last Message ID:** `{last_msg_id}`',
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    # 6. Handle User Request
    link_display = ""
    if isinstance(chat_id, int):
        try:
            chat_link = (await bot.create_chat_invite_link(chat_id)).invite_link
            link_display = chat_link
        except ChatAdminRequired:
            return await message.reply('❌ **Permission Error**\nPlease verify that I am an Admin in the target chat with "Invite Users" permission.')
    else:
        link_display = f"@{chat_id}"

    buttons = [
        [
            InlineKeyboardButton('✅ Accept Index',
                                 callback_data=f'index#accept#{chat_id}#{last_msg_id}#{message.from_user.id}')
        ],
        [
            InlineKeyboardButton('❌ Reject Index',
                                 callback_data=f'index#reject#{chat_id}#{message.id}#{message.from_user.id}'),
        ]
    ]

    await bot.send_message(
        LOG_CHANNEL,
        f'📝 **#IndexRequest**\n\n'
        f'**Requested By:** {message.from_user.mention} (`{message.from_user.id}`)\n'
        f'**Chat ID/User:** `{chat_id}`\n'
        f'**Last Message ID:** `{last_msg_id}`\n'
        f'**Link:** {link_display}',
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    await message.reply('✅ **Request Received**\n\nThank you for your contribution. Moderators will review your request shortly.')


@Client.on_message(filters.command('setskip') & filters.user(ADMINS))
async def set_skip_number(bot, message):
    if len(message.command) > 1:
        try:
            skip = int(message.command[1])
            temp.CURRENT = skip
            await message.reply(f"✅ **Skip Updated**\n\nSkipping first `{skip}` messages.")
        except ValueError:
            await message.reply("❌ **Error**\nValue must be an integer.")
    else:
        await message.reply("⚠️ **Usage:** `/setskip <number>`")


async def index_files_to_db(lst_msg_id, chat, msg, bot):
    """
    Iterates through messages and saves files to the database.
    """
    total_files = 0
    duplicate = 0
    errors = 0
    deleted = 0
    no_media = 0
    unsupported = 0
    
    current_skip = temp.CURRENT
    temp.CANCEL = False

    try:
        # iter_messages(chat_id, limit, offset)
        async for message in bot.iter_messages(chat, lst_msg_id, current_skip):
            if temp.CANCEL:
                break

            current_skip += 1

            # Update status every 20 messages to avoid FloodWait on edits
            if current_skip % 20 == 0:
                cancel_btn = [[InlineKeyboardButton('🚫 Cancel Indexing', callback_data='index_cancel')]]
                try:
                    await msg.edit_text(
                        text=f"**⚙️ Indexing in Progress...**\n\n"
                             f"📨 **Processed:** `{current_skip}`\n"
                             f"💾 **Saved:** `{total_files}`\n"
                             f"♻️ **Duplicates:** `{duplicate}`\n"
                             f"⚠️ **Errors:** `{errors}`",
                        reply_markup=InlineKeyboardMarkup(cancel_btn)
                    )
                except FloodWait as e:
                    await asyncio.sleep(e.value)
                except Exception:
                    pass

            if message.empty:
                deleted += 1
                continue
            
            if not message.media:
                no_media += 1
                continue

            if message.media not in [enums.MessageMediaType.VIDEO, enums.MessageMediaType.AUDIO, enums.MessageMediaType.DOCUMENT]:
                unsupported += 1
                continue

            media = getattr(message, message.media.value, None)
            if not media:
                unsupported += 1
                continue

            media.file_type = message.media.value
            media.caption = message.caption

            try:
                # Assuming save_file returns (success_bool, status_code)
                is_saved, status = await save_file(media)
                if is_saved:
                    total_files += 1
                elif status == 0:
                    duplicate += 1
                elif status == 2:
                    errors += 1
            except Exception as e:
                logger.error(f"Database save error: {e}")
                errors += 1

    except Exception as e:
        logger.exception(e)
        await msg.edit(f'❌ **Critical Error:** `{e}`')
    else:
        # Final Summary
        status_text = "🚫 **Indexing Cancelled**" if temp.CANCEL else "✅ **Indexing Completed**"
        
        await msg.edit(
            f"{status_text}\n\n"
            f"💾 **Total Saved:** `{total_files}`\n"
            f"♻️ **Duplicates Skipped:** `{duplicate}`\n"
            f"🗑️ **Deleted Messages:** `{deleted}`\n"
            f"📵 **No Media/Unsupported:** `{no_media + unsupported}`\n"
            f"⚠️ **Errors:** `{errors}`"
        )
