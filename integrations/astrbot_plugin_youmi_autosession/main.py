"""Rotate Youmi QQ conversations after a large model input."""

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Star
from astrbot.core.utils.session_lock import session_lock_manager

from .rotation import rotate


class YoumiAutoSession(Star):
    def __init__(self, context, config):
        super().__init__(context)
        self.config = config

    @filter.on_agent_done(priority=-100001)
    async def agent_done(self, event, run_context, response):
        state = event.get_extra('youmi_turn')
        if not state or state.source_kind == 'scheduled' or not event.get_extra('youmi_primary_chat', False):
            return
        usage = getattr(response, 'usage', None)
        tokens = getattr(usage, 'input', None)
        limit = self.config.get('input_token_limit', 32000)
        if type(tokens) is not int or type(limit) is not int or limit <= 0 or tokens < limit:
            return
        manager = self.context.conversation_manager
        conversation_id = await manager.get_curr_conversation_id(event.unified_msg_origin)
        if conversation_id:
            event.set_extra('youmi_rotation_conversation', conversation_id)

    @filter.after_message_sent(priority=-100001)
    async def after(self, event):
        state = event.get_extra('youmi_turn')
        conversation_id = event.get_extra('youmi_rotation_conversation')
        if not conversation_id or not state or not state.agent_done or not state.sent:
            return
        event.set_extra('youmi_rotation_conversation', None)
        try:
            if await rotate(self.context.conversation_manager, session_lock_manager,
                            event.unified_msg_origin, event.get_platform_id(), conversation_id):
                logger.info('Youmi conversation rotated after reaching the input token limit')
        except Exception:
            logger.warning('Youmi automatic conversation rotation failed', exc_info=True)
