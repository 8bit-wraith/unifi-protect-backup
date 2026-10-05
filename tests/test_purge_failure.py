import ast
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock


class Query:
    def __init__(self, db, sql):
        self.cursor = db.execute(sql)
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        self.cursor.close()
    def __aiter__(self):
        return self
    async def __anext__(self):
        row = self.cursor.fetchone()
        if row is None:
            raise StopAsyncIteration
        return row
    def __await__(self):
        async def result():
            return self
        return result().__await__()


class Database:
    def __init__(self):
        self.db = sqlite3.connect(':memory:')
        self.db.executescript('''
            PRAGMA foreign_keys=ON;
            CREATE TABLE events(id PRIMARY KEY, type, camera_id, start REAL, end REAL);
            CREATE TABLE backups(id REFERENCES events(id) ON DELETE CASCADE, remote, path, PRIMARY KEY(id,remote));
            INSERT INTO events VALUES('event1','motion','camera1',0,1);
            INSERT INTO backups VALUES('event1','mock','clip.mp4');
        ''')
    def execute(self, sql):
        return Query(self.db, sql)
    async def commit(self):
        self.db.commit()


class PurgeTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, returncode):
        tree = ast.parse((Path(__file__).parents[1]/'unifi_protect_backup/purge.py').read_text())
        nodes = [n for n in tree.body if isinstance(n, (ast.AsyncFunctionDef, ast.ClassDef))]
        command = AsyncMock(return_value=(returncode, '', 'synthetic failure' if returncode else ''))
        scope = dict(time=time, datetime=datetime, relativedelta=timedelta,
            aiosqlite=SimpleNamespace(Connection=object), logger=Mock(), run_command=command,
            wait_until=AsyncMock(side_effect=asyncio.CancelledError))
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'purge.py', 'exec'), scope)
        db = Database()
        self.addCleanup(db.db.close)
        purge = scope['Purge'](db, timedelta(days=1), 'mock:clips')
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(purge.start(), 1)
        return db.db.execute('SELECT COUNT(*) FROM events').fetchone()[0], db.db.execute('SELECT COUNT(*) FROM backups').fetchone()[0], command

    async def test_failed_delete_keeps_retry_records(self):
        events, backups, command = await self.exercise(7)
        self.assertEqual((events, backups), (1, 1))
        self.assertEqual(command.await_count, 1)

    async def test_success_deletes_records_and_tidies(self):
        events, backups, command = await self.exercise(0)
        self.assertEqual((events, backups), (0, 0))
        self.assertEqual(command.await_count, 2)

if __name__ == '__main__':
    unittest.main()
