import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location('youmi_network_web_patch',
    Path(__file__).resolve().parents[1] / 'web_patch.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

BASELINE = '''import json
class YoumiWeb:
    async def authorize(self, event):
        state = event.get_extra('youmi_turn')
        platform = event.get_platform_name()
        if (event.get_group_id() or event.get_platform_id() != self.config['qq_platform_id']
                or platform not in {'aiocqhttp', 'cron'} or not state
                or not state.prepared.get('turn_token') or not state.prepared.get('turn_key')
                or (platform != 'cron' and not event.get_extra('youmi_primary_chat', False))):
            raise ValueError('not_authorized')
        checked = await self.client.call('status', {'turn_token': state.prepared['turn_token']})
        if checked.get('turn_key') != state.prepared['turn_key']:
            raise ValueError('not_authorized')

    # Preserve the owner's separate modification.
    def other(self):
        return 'unchanged'

    @filter.llm_tool(name='xiaoxiao_web_search')
    async def search(self, event, query):
        return query
'''


class WebPatchTests(unittest.TestCase):
    def test_preserves_unrelated_user_code_and_adds_one_status_tool(self):
        result = module.patch_main(BASELINE)
        self.assertIn("    # Preserve the owner's separate modification.\n    def other(self):\n        return 'unchanged'", result)
        self.assertEqual(result.count("name='xiaoxiao_network_status'"), 1)
        self.assertIn('await _youmi_web_authorize(', result)
        self.assertIn("name='xiaoxiao_web_search'", result)

    def test_changed_authorization_is_rejected(self):
        source = BASELINE.replace("raise ValueError('not_authorized')", "raise ValueError('changed')", 1)
        with self.assertRaisesRegex(ValueError, 'authorize_preimage_changed'):
            module.patch_main(source)

    def test_second_application_is_rejected(self):
        with self.assertRaises(ValueError):
            module.patch_main(module.patch_main(BASELINE))


if __name__ == '__main__':
    unittest.main()
