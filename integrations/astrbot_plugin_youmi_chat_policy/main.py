"""Toggleable policy for authenticated Youmi foreground requests."""
from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Star

from .policy import apply_policy


class YoumiChatPolicy(Star):
    def __init__(self, context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

    @filter.on_llm_request(priority=-100000)
    async def request(self, event: AstrMessageEvent, req: ProviderRequest):
        if not event.get_extra('youmi_primary_chat', False) or not event.get_extra('youmi_turn'):
            return
        req.system_prompt = apply_policy(req.system_prompt or '', self.config.get('enabled', False))
