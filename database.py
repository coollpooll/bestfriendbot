import asyncpg

class Database:
    def __init__(self, dsn):
        self.dsn = dsn
        self.pool = None

    async def connect(self):
        self.pool = await asyncpg.create_pool(self.dsn)

    async def add_user(self, user_id):
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO users (user_id, requests_today) VALUES ($1, 0)
                ON CONFLICT (user_id) DO NOTHING
            ''', user_id)

    async def increment_request(self, user_id):
        async with self.pool.acquire() as conn:
            await conn.execute('''
                UPDATE users SET requests_today = requests_today + 1 WHERE user_id = $1
            ''', user_id)

    async def get_requests_today(self, user_id):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('''
                SELECT requests_today FROM users WHERE user_id = $1
            ''', user_id)
            return row['requests_today'] if row else 0

    async def reset_all_requests(self):
        async with self.pool.acquire() as conn:
            await conn.execute('''
                UPDATE users SET requests_today = 0
            ''')
