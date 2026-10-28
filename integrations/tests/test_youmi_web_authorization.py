import unittest
from types import SimpleNamespace

from integrations.astrbot_plugin_youmi_web.authorization import authorize


class Passport:
    def __init__(self, state):
        self.state = state
        self.handle = 'handle'
        self.message_obj = SimpleNamespace(session_id='session', message_id='turn')

    def get_extra(self, key, default=None):
        return self.state if key == 'youmi_turn' else None


class QQ:
    def __init__(self, state, group='', primary=True, platform='qq'):
        self.state, self.group, self.primary, self.platform = state, group, primary, platform

    def get_extra(self, key, default=None):
        return self.state if key == 'youmi_turn' else self.primary if key == 'youmi_primary_chat' else None

    def get_group_id(self):
        return self.group

    def get_platform_id(self):
        return self.platform

    def get_platform_name(self):
        return 'aiocqhttp'


class Client:
    def __init__(self, session='session', turn='turn', key='key'):
        self.session, self.turn, self.key, self.calls = session, turn, key, []

    async def call(self, name, body):
        self.calls.append(name)
        if name == 'passport-check':
            return {'session_id': self.session, 'turn_id': self.turn}
        return {'turn_key': self.key}


class WebAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_passport_requires_live_matching_device_turn(self):
        state = SimpleNamespace(prepared={'turn_token': 'token', 'turn_key': 'key'})
        client = Client()
        await authorize(Passport(state), client, 'qq', Passport)
        self.assertEqual(client.calls, ['passport-check', 'status'])
        for client in [Client(session='other'), Client(turn='other'), Client(key='other')]:
            with self.subTest(client=client), self.assertRaisesRegex(ValueError, 'not_authorized'):
                await authorize(Passport(state), client, 'qq', Passport)

    async def test_qq_private_primary_rules_are_preserved(self):
        state = SimpleNamespace(prepared={'turn_token': 'token', 'turn_key': 'key'})
        await authorize(QQ(state), Client(), 'qq', Passport)
        for event in [QQ(state, group='group'), QQ(state, primary=False), QQ(state, platform='other'), QQ(None)]:
            with self.subTest(event=event), self.assertRaisesRegex(ValueError, 'not_authorized'):
                await authorize(event, Client(), 'qq', Passport)


if __name__ == '__main__':
    unittest.main()
