import logging
from pyrogram import Client, filters, StopPropagation
from info import ADMINS
from utils import temp
from database.users_chats_db import db

logger = logging.getLogger(__name__)

# THE INTERCEPTOR: Runs on Group -1 (Before your standard Group 0 plugins)
@Client.on_message(filters.incoming, group=-1)
async def maintenance_interceptor(client, message):
    if temp.MAINTENANCE_MODE:
        # Let admins bypass the lock
        if message.from_user and message.from_user.id in ADMINS:
            return 
        
        # Block everyone else and kill the execution chain
        try:
            await message.reply("⚙️ **Bot is currently under maintenance.**\n\nWe are deploying upgrades or fixing issues. Please try again later.", quote=True)
        except Exception as e:
            logger.warning(f"Could not send maintenance message to {message.chat.id}: {e}")
        
        # This stops Pyrogram from checking any other handler for this message
        raise StopPropagation

# THE TOGGLE: Admin command to switch it on or off
@Client.on_message(filters.command("maintenance") & filters.user(ADMINS))
async def toggle_maintenance(client, message):
    if len(message.command) < 2 or message.command[1].lower() not in ['on', 'off']:
        return await message.reply("⚠️ **Invalid Format.**\nUse: `/maintenance on` or `/maintenance off`")

    status = message.command[1].lower() == 'on'
    
    if temp.MAINTENANCE_MODE == status:
        return await message.reply(f"Maintenance mode is already **{'ON' if status else 'OFF'}**.")

    # Update cache and database
    temp.MAINTENANCE_MODE = status
    await db.set_maintenance(status)
    
    await message.reply(f"✅ **Maintenance mode has been turned {'ON' if status else 'OFF'}.**")
