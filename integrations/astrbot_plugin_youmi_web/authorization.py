"""Authorize web tools against the current QQ or live Passport turn."""


async def authorize(event, client, qq_platform_id, passport_type):
    state = event.get_extra('youmi_turn')
    if not state or not state.prepared.get('turn_token') or not state.prepared.get('turn_key'):
        raise ValueError('not_authorized')
    if isinstance(event, passport_type):
        current = await client.call('passport-check', {'handle': event.handle})
        if (current.get('session_id') != event.message_obj.session_id
                or current.get('turn_id') != event.message_obj.message_id):
            raise ValueError('not_authorized')
    else:
        platform = event.get_platform_name()
        if (event.get_group_id() or event.get_platform_id() != qq_platform_id
                or platform not in {'aiocqhttp', 'cron'}
                or (platform != 'cron' and not event.get_extra('youmi_primary_chat', False))):
            raise ValueError('not_authorized')
    checked = await client.call('status', {'turn_token': state.prepared['turn_token']})
    if checked.get('turn_key') != state.prepared['turn_key']:
        raise ValueError('not_authorized')
