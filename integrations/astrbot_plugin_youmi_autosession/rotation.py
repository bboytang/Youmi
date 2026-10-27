"""Rotate only the conversation that generated an accepted QQ reply."""


async def rotate(manager, lock_manager, origin, platform_id, expected_id):
    async with lock_manager.acquire_lock(origin):
        if await manager.get_curr_conversation_id(origin) != expected_id:
            return False
        old = await manager.get_conversation(origin, expected_id)
        if old is None:
            return False
        await manager.new_conversation(origin, platform_id, persona_id=old.persona_id)
        return True
