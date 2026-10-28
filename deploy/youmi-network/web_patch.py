"""Prepare a narrow patch while preserving production web-plugin changes."""

import ast
import hashlib


AUTHORIZE_AST_SHA256 = 'd9d331d79f9db0b307780cf3b3192d477de47ea572b4801d11b79184bae43201'


def patch_main(source):
    tree = ast.parse(source)
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'YoumiWeb']
    if len(classes) != 1:
        raise ValueError('unexpected_web_class')
    cls = classes[0]
    methods = [n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'authorize']
    searches = [n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'search']
    if len(methods) != 1 or len(searches) != 1 or any(
            isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == 'network_status'
            for n in cls.body):
        raise ValueError('unexpected_web_methods')
    fingerprint = hashlib.sha256(ast.dump(methods[0], include_attributes=False).encode()).hexdigest()
    if fingerprint != AUTHORIZE_AST_SHA256:
        raise ValueError('authorize_preimage_changed')
    if any(isinstance(n, ast.ImportFrom) and n.module in {'authorization', 'network_health'}
           for n in tree.body):
        raise ValueError('network_import_already_exists')

    newline = '\r\n' if '\r\n' in source else '\n'
    lines = source.splitlines(keepends=True)
    imports = ("from ..astrbot_plugin_youmi.passport import PassportEvent" + newline
               + "from .authorization import authorize as _youmi_web_authorize" + newline
               + "from .network_health import status as _youmi_network_status" + newline + newline)
    method = ("    async def authorize(self, event):" + newline
              + "        await _youmi_web_authorize(event, self.client, self.config['qq_platform_id'], PassportEvent)" + newline)
    status = ("    @filter.llm_tool(name='xiaoxiao_network_status')" + newline
              + "    async def network_status(self, event: AstrMessageEvent):" + newline
              + '        """检查联网代理、浏览器和统一网络服务的本地端口是否可达。"""' + newline
              + "        try:" + newline
              + "            await self.authorize(event)" + newline
              + "            return json.dumps(await _youmi_network_status(), ensure_ascii=False)" + newline
              + "        except (ValueError, KeyError, BridgeError) as error:" + newline
              + "            return json.dumps({'error': str(error)[:120]}, ensure_ascii=False)" + newline
              + "        except Exception:" + newline
              + "            return json.dumps({'error': 'network_status_unavailable'})" + newline + newline)
    class_start = min([cls.lineno] + [n.lineno for n in cls.decorator_list]) - 1
    search_start = min([searches[0].lineno] + [n.lineno for n in searches[0].decorator_list]) - 1
    changes = [(class_start, class_start, imports),
               (methods[0].lineno - 1, methods[0].end_lineno, method),
               (search_start, search_start, status)]
    for start, end, replacement in sorted(changes, reverse=True):
        lines[start:end] = [replacement]
    patched = ''.join(lines)
    ast.parse(patched)
    return patched
