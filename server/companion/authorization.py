"""Private scopes come from manager verification, never from device JSON."""

import asyncio
import base64
import hmac
from dataclasses import dataclass
from datetime import datetime, timezone
import ipaddress
import json
import os
import re
import ssl
from urllib.parse import urlsplit


class PrivateAuthorizationRequired(Exception):
    def __init__(self):
        super().__init__("请用智控台授权码完成授权")


def server_tls_context():
    certificate = os.environ.get("YOUMI_TLS_CERT_FILE") or os.environ.get("MIA_TLS_CERT_FILE")
    key = os.environ.get("YOUMI_TLS_KEY_FILE") or os.environ.get("MIA_TLS_KEY_FILE")
    if not certificate and not key:
        return None
    if not certificate or not key:
        raise ValueError("Both Youmi TLS certificate and key paths are required")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, key)
    return context


@dataclass(frozen=True)
class AuthenticatedScope:
    owner: str
    operator_id: int
    user_id: int
    agent_id: str
    persona_id: str
    device_id: str
    client_id: str
    binding_version: int
    credential_version: int
    expires_at: datetime
    permissions: frozenset

    def snapshot(self):
        # No credential or model configuration is persisted with worker jobs.
        return {name: getattr(self, name) for name in (
            "owner", "operator_id", "device_id", "client_id", "binding_version", "credential_version")}


def _manager_scope(data):
    try:
        data = dict(data)
        integers = ("operator_id", "user_id", "binding_version", "credential_version")
        # The existing manager deliberately serializes Java Long as decimal text.
        # Normalize only exact canonical integers from the verified response.
        for name in integers:
            value = data[name]
            if isinstance(value, str) and re.fullmatch(r"0|[1-9][0-9]{0,18}", value):
                data[name] = int(value)
        if any(type(data[name]) is not int or data[name] < (0 if name == "binding_version" else 1)
               or data[name] > 2**63 - 1
               for name in integers):
            raise ValueError()
        for name in ("agent_id", "device_id", "client_id"):
            if not isinstance(data[name], str) or not re.fullmatch(r"[A-Za-z0-9_:.-]{1,128}", data[name]):
                raise ValueError()
        if data["owner"] != f'user:{data["user_id"]}:agent:{data["agent_id"]}' or data["persona_id"] != "mia":
            raise ValueError()
        permissions = data["permissions"]
        allowed = {"memory:read", "memory:write", "memory:manage"}
        if not isinstance(permissions, list) or any(type(p) is not str or p not in allowed for p in permissions):
            raise ValueError()
        expiry = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))
        if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
            raise ValueError()
        return AuthenticatedScope(**{name: data[name] for name in (
            "owner", "operator_id", "user_id", "agent_id", "persona_id", "device_id", "client_id",
            "binding_version", "credential_version")}, expires_at=expiry, permissions=frozenset(permissions))
    except (KeyError, TypeError, ValueError, AttributeError):
        raise PrivateAuthorizationRequired() from None


class PrivateAuthorization:
    def __init__(self, config, *, connection_loop=None):
        self.config = config
        self._connection_loop = connection_loop
        self._session = None
        self._closed = False

    async def close(self):
        self._closed = True
        session, self._session = self._session, None
        if session is not None:
            await session.aclose()

    async def _request(self, endpoint, body, timeout=3):
        # Reuse the existing server-secret client, with one bounded attempt.
        # Public HTTP is never permitted. Loopback is for isolated CI only;
        # an internal production transport must be reviewed before adding it.
        from config.manage_api_client import ManageApiClient

        try:
            manager = self.config["manager-api"]
            url = urlsplit(manager["url"])
            try:
                loopback = ipaddress.ip_address(url.hostname).is_loopback
            except ValueError:
                loopback = False
            if (url.scheme not in {"https", "http"} or (url.scheme == "http" and not loopback)
                    or url.username or url.password or url.query or url.fragment):
                raise PrivateAuthorizationRequired()
            client = ManageApiClient._get_instance()
            if self._closed or client is None or client.config.get("url") != manager["url"]:
                raise PrivateAuthorizationRequired()
            async def send():
                if self._closed:
                    raise PrivateAuthorizationRequired()
                # The deployed manager client owns a session per request; the
                # fixed upstream client owns sessions per event loop. Both paths
                # call the single-attempt method, never its retry wrapper.
                if callable(getattr(client, "_build_client", None)):
                    if asyncio.get_running_loop() is self._connection_loop:
                        # Reuse transport only. Every caller still makes a fresh
                        # manager request and validates its current Scope.
                        if self._session is None:
                            self._session = client._build_client()
                        return await client._async_request(self._session, "POST", endpoint, json=body, timeout=timeout)
                    async with client._build_client() as session:
                        return await client._async_request(session, "POST", endpoint, json=body, timeout=timeout)
                return await client._async_request("POST", endpoint, json=body, timeout=timeout)
            result = await asyncio.wait_for(send(), timeout=timeout)
            if self._closed:
                raise PrivateAuthorizationRequired()
            return result
        except Exception:
            # Never propagate HTTP request bodies, credentials or provider errors.
            raise PrivateAuthorizationRequired() from None

    async def verify(self, device_id, client_id, credential):
        if not isinstance(credential, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", credential):
            raise PrivateAuthorizationRequired()
        scope = _manager_scope(await self._request("/config/youmi-auth/verify", {
            "device_id": device_id, "client_id": client_id, "private_credential": credential}))
        if scope.device_id != device_id or scope.client_id != client_id:
            raise PrivateAuthorizationRequired()
        return scope

    async def restore(self, snapshot):
        # Persisted server-owned snapshots still require current manager proof.
        actual = _manager_scope(await self._request("/config/youmi-auth/check-scope", snapshot))
        if actual.snapshot() != snapshot:
            raise PrivateAuthorizationRequired()
        return actual

    async def require(self, scope, permission):
        if (not isinstance(scope, AuthenticatedScope) or permission not in scope.permissions
                or scope.expires_at <= datetime.now(timezone.utc)):
            raise PrivateAuthorizationRequired()
        actual = await self.restore(scope.snapshot())
        if permission not in actual.permissions or actual != scope:
            raise PrivateAuthorizationRequired()
        return actual

    async def worker_model(self, scope):
        await self.require(scope, 'memory:write')
        models = await self._request('/config/youmi-auth/worker-model', scope.snapshot())
        if not isinstance(models, dict) or _manager_scope(models.get('youmi_scope', models.get('mia_scope'))) != scope:
            raise PrivateAuthorizationRequired()
        return models

    async def private_config(self, device_id, client_id, credential, selected_module):
        scope = await self.verify(device_id, client_id, credential)
        models = await self._request("/config/agent-models", {
            "macAddress": device_id, "clientId": client_id, "privateCredential": credential,
            "selectedModule": selected_module})
        if not isinstance(models, dict) or models.get("youmi_owner", models.get("mia_owner")) != scope.owner:
            raise PrivateAuthorizationRequired()
        if _manager_scope(models.get("youmi_scope", models.get("mia_scope"))) != scope:
            raise PrivateAuthorizationRequired()
        # Correct words cannot be loaded independently of private authorization.
        words = await self._request("/config/correct-words", {
            "macAddress": device_id, "clientId": client_id, "privateCredential": credential})
        if not isinstance(words, list) or any(not isinstance(word, str) for word in words):
            raise PrivateAuthorizationRequired()
        await self.require(scope, "memory:read")
        models["correct_words"] = words
        return scope, models


async def load_private_config(conn):
    conn._youmi_scope = None
    conn._youmi_owner = None
    conn._youmi_authorization = PrivateAuthorization(conn.config, connection_loop=asyncio.get_running_loop())
    headers = conn.headers or {}
    credential = headers.pop("youmi-private-credential", None)
    legacy_credential = headers.pop("mia-private-credential", None)
    if credential and legacy_credential and credential != legacy_credential:
        raise PrivateAuthorizationRequired()
    credential = credential or legacy_credential
    # A private header must arrive over TLS. A future TLS proxy requires an
    # independently verified trusted ingress, never X-Forwarded-Proto alone.
    transport = getattr(getattr(conn, "websocket", None), "transport", None)
    if credential and (transport is None or not transport.get_extra_info("ssl_object")):
        raise PrivateAuthorizationRequired()
    device = headers.get("device-id")
    client = headers.get("client-id", device)
    try:
        scope, config = await asyncio.wait_for(conn._youmi_authorization.private_config(
            device, client, credential, conn.config["selected_module"]), timeout=3)
    except asyncio.TimeoutError:
        await conn._youmi_authorization.close()
        raise PrivateAuthorizationRequired() from None
    except BaseException:
        await conn._youmi_authorization.close()
        raise
    conn._youmi_scope = scope
    conn._youmi_owner = scope.owner
    return config


async def close_connection(conn):
    authorization = getattr(conn, '_youmi_authorization', None)
    if isinstance(authorization, PrivateAuthorization):
        await authorization.close()


async def require_connection(conn, permission="memory:read"):
    if not getattr(conn, "read_config_from_api", False):
        return
    authorization = getattr(conn, "_youmi_authorization", None)
    if not authorization:
        raise PrivateAuthorizationRequired()
    await authorization.require(getattr(conn, "_youmi_scope", None), permission)


async def report_chat(conn, mac_address, session_id, chat_type, content, audio, report_time):
    if not content:
        return None
    await require_connection(conn, "memory:write")
    scope = getattr(conn, "_youmi_scope", None)
    if not isinstance(scope, AuthenticatedScope) or scope.device_id != mac_address or conn.session_id != session_id:
        raise PrivateAuthorizationRequired()
    return await conn._youmi_authorization._request("/agent/chat-history/report", {
        "macAddress": mac_address, "sessionId": session_id, "chatType": chat_type,
        "content": content, "reportTime": report_time,
        "audioBase64": base64.b64encode(audio).decode("utf-8") if audio else None,
        "miaScope": scope.snapshot()})


async def save_chat_title(conn):
    scope = getattr(conn, "_youmi_scope", None)
    if not isinstance(scope, AuthenticatedScope) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", conn.session_id):
        raise PrivateAuthorizationRequired()
    authorization = getattr(conn, '_youmi_authorization', None)
    if isinstance(authorization, PrivateAuthorization) and authorization._closed:
        # The existing title worker outlives the websocket. Revalidate its
        # server-owned Scope with a separate, non-pooled client, like jobs.
        authorization = PrivateAuthorization(conn.config)
    if authorization is None:
        raise PrivateAuthorizationRequired()
    await authorization.require(scope, 'memory:read')
    # Existing title generation remains synchronous at manager; one attempt.
    return await authorization._request(
        f"/agent/chat-title/{conn.session_id}/generate", scope.snapshot(), timeout=30)


async def prompt_authorization(conn):
    await conn.websocket.send(json.dumps({
        "type": "mia_authorization", "status": "required",
        "message": "请用智控台授权码完成授权", "session_id": conn.session_id}, ensure_ascii=False))


async def route_server_control(conn, message):
    # Console control has a server identity, never a private companion identity.
    if not isinstance(message, str) or len(message) > 16384:
        return False
    try:
        command = json.loads(message)
    except (ValueError, RecursionError):
        return False
    if not isinstance(command, dict) or command.get('type') != 'server':
        return False
    secret = conn.config.get('manager-api', {}).get('secret', '')
    content = command.get('content')
    supplied = content.get('secret') if isinstance(content, dict) else None
    transport = getattr(conn.websocket, 'transport', None)
    if (not conn.read_config_from_api or transport is None
            or not transport.get_extra_info('ssl_object')
            or not isinstance(secret, str) or not secret
            or not isinstance(supplied, str) or not hmac.compare_digest(secret.encode(), supplied.encode())
            or command.get('action') not in {'update_config', 'restart'}):
        await conn.websocket.send(json.dumps({'type': 'server', 'status': 'error', 'message': '管理命令未获授权'}))
        return True
    from core.handle.textHandler.serverMessageHandler import ServerTextMessageHandler
    await ServerTextMessageHandler().handle(conn, command)
    return True


async def route_required(conn, message):
    # Keep the transport hello available, but no personalized audio/text route.
    if isinstance(message, str) and len(message) <= 16384:
        try:
            request = json.loads(message)
        except (ValueError, RecursionError):
            request = None
        if isinstance(request, dict) and request.get("type") == "hello":
            from core.handle.textHandle import handleTextMessage
            await handleTextMessage(conn, message)
            await prompt_authorization(conn)


async def allow_output(conn):
    try:
        await require_connection(conn)
        return True
    except PrivateAuthorizationRequired:
        conn.client_abort = True
        if not getattr(conn, "_youmi_authorization_required", False):
            conn._youmi_authorization_required = True
            await prompt_authorization(conn)
        return False


def allow_generation(conn):
    # Upstream chat() runs in its executor, including recursive tool continuations.
    if not getattr(conn, "read_config_from_api", False):
        return True
    try:
        return asyncio.run_coroutine_threadsafe(allow_output(conn), conn.loop).result(timeout=4)
    except Exception:
        conn.client_abort = True
        return False


async def execute_tool(conn, tool_call):
    await require_connection(conn)
    if hasattr(conn, '_youmi_context_builder'):
        from .reply import validate
        from .context import ContextError
        turn = await validate(conn)
        allowed = {tool['function']['name'] for tool in turn.bundle.request().get('tools', [])}
        if tool_call.get('name') not in allowed:
            raise ContextError('context_tool_not_declared')
    return await conn.func_handler.handle_llm_function_call(conn, tool_call)


async def intent_reply(conn, context, query):
    await require_connection(conn)
    return await conn.intent.replyResult(context, query)
