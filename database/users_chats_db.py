import pytz
from datetime import date, datetime, timedelta
import motor.motor_asyncio
from info import DATABASE_NAME, DATABASE_URI, IMDB, IMDB_TEMPLATE, MELCOW_NEW_USERS, P_TTI_SHOW_OFF, SINGLE_BUTTON, SPELL_CHECK_REPLY, PROTECT_CONTENT

class Database:
    
    def __init__(self, uri, database_name):
        self._client = motor.motor_asyncio.AsyncIOMotorClient(uri)
        self.db = self._client[database_name]
        self.col = self.db.users
        self.grp = self.db.groups


    def new_user(self, id, name):
        tz = pytz.timezone('Asia/Kolkata')
        return dict(
            id = id,
            name = name,
            ban_status=dict(
                is_banned=False,
                ban_reason="",
            ),
            timestamp=datetime.now(tz)
        )


    def new_group(self, id, title):
        tz = pytz.timezone('Asia/Kolkata')
        return dict(
            id = id,
            title = title,
            chat_status=dict(
                is_disabled=False,
                reason="",
            ),
            timestamp=datetime.now(tz)
        )
    
    async def track_user_activity(self, id, name):
        """Track or update user activity timestamp."""
        tz = pytz.timezone('Asia/Kolkata')
        today = datetime.now(tz)
        if not await self.is_user_exist(id):
            user = self.new_user(id, name)
            await self.col.insert_one(user)
        else:
            await self.col.update_one(
                {"id": id},
                {"$set": {"last_active": today}}
            )

    async def get_bot_status(self):
        """Get total users, active users, and active user percentage."""
        tz = pytz.timezone('Asia/Kolkata')
        today_start = tz.localize(datetime.combine(date.today(), datetime.min.time()))
        today_end = tz.localize(datetime.combine(date.today(), datetime.max.time()))

        # Total users count
        total_users = await self.col.count_documents({})
        
        # Daily active users count
        daily_active_users = await self.col.count_documents({
            "last_active": {"$gte": today_start, "$lt": today_end}
        })
        
        # Calculate percentage of active users
        active_user_percentage = (daily_active_users / total_users * 100) if total_users > 0 else 0

        return {
            "total_users": total_users,
            "daily_active_users": daily_active_users,
            "active_user_percentage": active_user_percentage
        }
        
    async def daily_users_count(self, today):
        tz = pytz.timezone('Asia/Kolkata')
        start = tz.localize(datetime.combine(today, datetime.min.time()))
        end = tz.localize(datetime.combine(today, datetime.max.time()))
        count = await self.col.count_documents({
            'timestamp': {'$gte': start, '$lt': end}
        })
        return count
    
    async def daily_chats_count(self, today):
        tz = pytz.timezone('Asia/Kolkata')
        start = tz.localize(datetime.combine(today, datetime.min.time()))
        end = tz.localize(datetime.combine(today, datetime.max.time()))
        count = await self.grp.count_documents({
            'timestamp': {'$gte': start, '$lt': end}
        })
        return count

    async def save_chat_invite_link(self, chat_id, invite_link):
        await self.grp.update_one({'id': int(chat_id)}, {'$set': {'invite_link': invite_link}})
    
    async def get_chat_invite_link(self, chat_id):
        chat = await self.grp.find_one({'id': int(chat_id)})
        if chat:
            return chat.get('invite_link', None)
        return None

    async def update_verification(self, id, short, date, time):
        status = {
            'short': str(short),
            'date': str(date),
            'time': str(time)
        }
        await self.col.update_one({'id': int(id)}, {'$set': {'verification_status': status}})
    
    async def get_verified(self, id):
        default = {
            'short': "1",
            'date': "1999-12-31",
            'time': "23:59:59"
        }
        user = await self.col.find_one({'id': int(id)})
        if user:
            return user.get("verification_status", default)
        return default
        
    async def add_user(self, id, name):
        user = self.new_user(id, name)
        await self.col.insert_one(user)
    
    async def is_user_exist(self, id):
        user = await self.col.find_one({'id':int(id)})
        return bool(user)
    
    async def total_users_count(self):
        count = await self.col.count_documents({})
        return count
    
    async def remove_ban(self, id):
        ban_status = dict(
            is_banned=False,
            ban_reason=''
        )
        await self.col.update_one({'id': id}, {'$set': {'ban_status': ban_status}})
    
    async def ban_user(self, user_id, ban_reason="No Reason"):
        ban_status = dict(
            is_banned=True,
            ban_reason=ban_reason
        )
        await self.col.update_one({'id': user_id}, {'$set': {'ban_status': ban_status}})

    async def get_ban_status(self, id):
        default = dict(
            is_banned=False,
            ban_reason=''
        )
        user = await self.col.find_one({'id':int(id)})
        if not user:
            return default
        return user.get('ban_status', default)

    async def get_all_users(self):
        return self.col.find({})
    
    async def delete_user(self, user_id):
        await self.col.delete_many({'id': int(user_id)})

    async def get_banned(self):
        users = self.col.find({'ban_status.is_banned': True})
        chats = self.grp.find({'chat_status.is_disabled': True})
        b_chats = [chat['id'] async for chat in chats]
        b_users = [user['id'] async for user in users]
        return b_users, b_chats
    
    async def add_chat(self, chat, title):
        chat = self.new_group(chat, title)
        await self.grp.insert_one(chat)
    
    async def get_chat(self, chat):
        chat = await self.grp.find_one({'id':int(chat)})
        return False if not chat else chat.get('chat_status')
    
    async def total_chat_count(self):
        count = await self.grp.count_documents({})
        return count
    
    async def re_enable_chat(self, id):
        chat_status=dict(
            is_disabled=False,
            reason="",
        )
        await self.grp.update_one({'id': int(id)}, {'$set': {'chat_status': chat_status}})
        
    async def update_settings(self, id, settings):
        await self.grp.update_one({'id': int(id)}, {'$set': {'settings': settings}})
        
    async def get_settings(self, id):
        default = {
            'button': SINGLE_BUTTON,
            'botpm': P_TTI_SHOW_OFF,
            'file_secure': PROTECT_CONTENT,
            'imdb': IMDB,
            'spell_check': SPELL_CHECK_REPLY,
            'welcome': MELCOW_NEW_USERS,
            'template': IMDB_TEMPLATE
        }
        chat = await self.grp.find_one({'id':int(id)})
        if chat:
            return chat.get('settings', default)
        return default
    
    async def disable_chat(self, chat, reason="No Reason"):
        chat_status=dict(
            is_disabled=True,
            reason=reason,
        )
        await self.grp.update_one({'id': int(chat)}, {'$set': {'chat_status': chat_status}})
    
    async def get_all_chats(self):
        return self.grp.find({})

    async def delete_chat(self, chat):
        await self.grp.delete_many({'id': int(chat)})
        
    async def get_db_size(self):
        return (await self.db.command("dbstats"))['dataSize']
    
    async def get_users(self, limit):
        # Return the users as a list with a limit
        return await self.col.find({}).limit(limit).to_list(length=limit)

    # Add these to database/users_chats_db.py inside the Database class
    async def set_maintenance(self, status: bool):
        await self.db.settings.update_one(
            {'id': 'bot_maintenance'}, 
            {'$set': {'status': status}}, 
            upsert=True
        )

    async def get_maintenance(self) -> bool:
        doc = await self.db.settings.find_one({'id': 'bot_maintenance'})
        return doc['status'] if doc else False


db = Database(DATABASE_URI, DATABASE_NAME)
