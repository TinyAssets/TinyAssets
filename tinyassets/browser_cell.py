"""Fixed Playwright worker. Private namespace, ephemeral profile, trusted IPC only."""
from __future__ import annotations

import asyncio
import base64
import json
import re
import secrets
import sys
from urllib.parse import urlsplit

from tinyassets.browser_egress import origin

BOUND = 8 * 1024 * 1024
SECRET_FIELD = 'input[type=password],input[autocomplete=one-time-code]'


def emit(value):
    raw = json.dumps(value).encode() + b'\n'
    if len(raw) > BOUND:
        raise ValueError('browser packet exceeds bound')
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()


class Browser:
    def __init__(self):
        self.pending = {}
        self.queue = asyncio.Queue()
        self.sequence = 0
        self.capture = True
        self.page = None
        self.hooks = {}
        self.held = set()
        self.navigation_failed = False
        self.blocked = False
        self.needs_owner = False
        self.completed_steps = 0
        self.field = None

    async def receive(self):
        while True:
            raw = await asyncio.to_thread(sys.stdin.buffer.readline, BOUND + 1)
            if not raw:
                await self.queue.put(None)
                return
            if len(raw) > BOUND or not raw.endswith(b'\n'):
                raise ValueError('invalid browser packet')
            value = json.loads(raw)
            if 'network_reply' in value:
                future = self.pending.pop(value['network_reply'], None)
                if future and not future.done():
                    future.set_result(value)
            else:
                await self.queue.put(value)

    async def route(self, session, event):
        request_id = event['requestId']
        try:
            request = event['request']
            target = origin(request['url'])
            if not self.capture and event['resourceType'] == 'Document' and target != self.site:
                self.needs_owner = True
                raise PermissionError('navigation outside connected site')
            self.sequence += 1
            number = self.sequence
            future = asyncio.get_running_loop().create_future()
            self.pending[number] = future
            body = request.get('postData', '').encode()
            if request.get('postDataEntries'):
                body = b''.join(base64.b64decode(entry['bytes'])
                                for entry in request['postDataEntries'])
            emit({'network': number, 'request': dict(url=request['url'], method=request['method'],
                  headers=request['headers'], body=base64.b64encode(body).decode())})
            reply = await asyncio.wait_for(future, 35)
            if 'error' in reply:
                raise PermissionError('network refused')
            else:
                result = reply['response']
                if event['resourceType'] == 'Document' and result['status'] in {403, 429, 503}:
                    self.blocked = True
                headers = [{'name': name, 'value': part}
                           for name, value in result['headers'].items()
                           for part in value.split('\n')]
                await session.send('Fetch.fulfillRequest', dict(requestId=request_id,
                    responseCode=result['status'], responseHeaders=headers, body=result['body']))
        except Exception:  # noqa: BLE001 - never print Playwright/network secrets
            if event['resourceType'] == 'Document':
                self.navigation_failed = True
            await session.send('Fetch.failRequest', dict(requestId=request_id,
                                                        errorReason='BlockedByClient'))

    async def hook(self, page):
        session = await self.context.new_cdp_session(page)
        session.on('Fetch.requestPaused', lambda event: self.route(session, event))
        await session.send('Fetch.enable', {'patterns': [{'urlPattern': '*'}]})
        page.on('requestfailed', lambda request: self.failed(request))

    def failed(self, request):
        if request.is_navigation_request():
            self.navigation_failed = True

    def remember(self, state):
        def values(value):
            if isinstance(value, str):
                yield value
            elif isinstance(value, dict):
                for item in value.values():
                    yield from values(item)
            elif isinstance(value, list):
                for item in value:
                    yield from values(item)

        self.held.update(cookie['value'] for cookie in state.get('cookies', []))
        for record in state.get('origins', []):
            self.held.update(item['value'] for item in record.get('localStorage', []))
            self.held.update(values(record.get('indexedDB', [])))

    def opened(self, page):
        self.page = page
        self.hooks[page] = asyncio.create_task(self.hook(page))
        page.set_default_timeout(10000)
        page.on('dialog', lambda dialog: dialog.dismiss())
        page.on('framenavigated', self.navigated)

    def navigated(self, frame):
        if self.field and self.field['frame'] == frame:
            self.field = None

    async def challenged(self):
        if self.navigation_failed or origin(self.page.url) != self.site:
            return True
        if re.search(r'/(login|signin|sign-in|challenge|captcha)(/|$)',
                     urlsplit(self.page.url).path, re.I):
            return True
        for frame in self.page.frames:
            for field in await frame.locator(SECRET_FIELD).all():
                if await field.is_visible():
                    return True
        return False

    async def verified(self):
        if self.blocked or await self.challenged():
            return False
        return await self.page.locator(self.verify['selector']).is_visible()

    async def field_binding(self):
        """Retain an element handle, never a selector that can retarget a new page."""
        focused = None
        for frame in self.page.frames:
            focused = await frame.query_selector('input:focus,textarea:focus')
            if focused:
                break
        if focused is None:
            self.field = None
            return None
        previous = self.field
        if (previous and previous['page'] == self.page and previous['frame'] == frame
                and await focused.evaluate('(node, old) => node === old', previous['node'])
                and self.field is previous):
            return {key: previous[key] for key in ('token', 'origin', 'type')}
        kind = await focused.get_attribute('type') or 'text'
        self.field = dict(node=focused, page=self.page, frame=frame, token=secrets.token_hex(24),
                          origin=origin(frame.url), type=kind)
        return {key: self.field[key] for key in ('token', 'origin', 'type')}

    async def protected_fill(self, command):
        field = self.field
        self.field = None  # One use, including refused attempts.
        if (not field or command.get('token') != field['token']
                or command.get('origin') != field['origin'] or self.page != field['page']
                or origin(field['frame'].url) != field['origin']):
            raise PermissionError('credential destination changed')
        value = command.get('value')
        if not isinstance(value, str) or len(value) > 4096:
            raise ValueError('invalid protected input')
        # Check and assign in one renderer operation: no navigation TOCTOU.
        accepted = await field['node'].evaluate('''(node, data) => {
          if (!node.isConnected || node.ownerDocument !== document ||
              document.activeElement !== node || location.origin !== data.origin) return false;
          const start=node.selectionStart ?? node.value.length;
          const end=node.selectionEnd ?? start;
          const value=data.insert ?
            node.value.slice(0,start)+data.value+node.value.slice(end) : data.value;
          Object.getOwnPropertyDescriptor(Object.getPrototypeOf(node),'value').set.call(node,value);
          if(data.insert && node.setSelectionRange)
            node.setSelectionRange(start+data.value.length,start+data.value.length);
          node.dispatchEvent(new Event('input', {bubbles:true}));
          node.dispatchEvent(new Event('change', {bubbles:true})); return true;
        }''', {'origin': field['origin'], 'value': value, 'insert': command.get('insert', False)})
        if not accepted:
            raise PermissionError('credential destination changed')
        self.held.add(value)
        return {'status': 'login'}

    async def command(self, command, playwright):
        action = command['action']
        if action == 'open':
            self.capture = command['capture']
            self.site = origin(command['url'])
            self.verify = command['verify']
            self.remember(command.get('state') or {})
            self.browser = await playwright.chromium.launch(chromium_sandbox=True,
                args=['--disable-dev-shm-usage', '--disable-quic'])
            self.context = await self.browser.new_context(
                storage_state=command.get('state') or None, accept_downloads=False,
                service_workers='block', viewport={'width': 1024, 'height': 768})
            self.context.on('page', self.opened)
            self.page = await self.context.new_page()
            await self.hooks[self.page]
            await self.page.goto(command['url'], wait_until='domcontentloaded')
            if self.blocked:
                return {'status': 'blocked', 'blocked': True}
            return {'status': 'login' if self.capture else 'ready'}
        if self.page is None:
            raise ValueError('browser not open')
        if self.page.is_closed():
            self.page = self.context.pages[-1]
        if action == 'frame' and self.capture:
            if self.blocked:
                return {'status': 'blocked', 'blocked': True}
            if await self.verified():
                return {'state': await self.context.storage_state(indexed_db=True),
                        'status': 'verified'}
            picture = await self.page.screenshot(type='jpeg', quality=75)
            return {'image': base64.b64encode(picture).decode(),
                    'origin': origin(self.page.url), 'width': 1024, 'height': 768,
                    'field': await self.field_binding()}
        if action == 'fill_private' and self.capture:
            return await self.protected_fill(command)
        if action == 'input' and self.capture:
            event = command['event']
            if event.get('origin') != origin(self.page.url):
                raise PermissionError('input origin changed; refresh the view')
            if event['kind'] == 'click':
                await self.page.mouse.click(float(event['x']), float(event['y']))
            elif event['kind'] == 'text':
                if not isinstance(event['text'], str) or len(event['text']) > 4096:
                    raise ValueError('invalid text')
                binding = await self.field_binding()
                if not binding:
                    raise PermissionError('select a website field first')
                await self.protected_fill({**binding, 'value': event['text'], 'insert': True})
            elif event['kind'] == 'key' and event['key'] in {
                    'Tab', 'Shift+Tab', 'Enter', 'Backspace', 'Delete', 'Escape',
                    'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Control+A'}:
                await self.page.keyboard.press(event['key'])
            elif event['kind'] == 'scroll':
                await self.page.mouse.wheel(float(event.get('x', 0)), float(event['y']))
            else:
                raise ValueError('invalid owner input')
            return {'status': 'login'}
        if action in {'save', 'verify'}:
            if not await self.verified():
                if self.blocked:
                    return {'status': 'blocked', 'blocked': True}
                return {'needs_login': True}
            return {'state': await self.context.storage_state(indexed_db=True)}
        if action != 'steps' or self.capture:
            raise PermissionError('browser operation refused')
        steps = command['steps']
        if not isinstance(steps, list) or len(steps) > 20:
            raise ValueError('invalid steps')
        for step in steps:
            if self.blocked:
                return {'status': 'blocked', 'blocked': True}
            if await self.challenged():
                return {'needs_login': True}
            kind = step['kind']
            if kind == 'navigate':
                if origin(step['url']) != self.site:
                    raise PermissionError('navigation outside connected site')
                await self.page.goto(step['url'], wait_until='domcontentloaded')
            elif kind in {'click', 'fill', 'press'}:
                # Selectors are data, never page scripts. Passwords and OTPs are owner-only.
                target = self.page.locator(step['selector'])
                if (await target.get_attribute('type') == 'password'
                        or await target.get_attribute('autocomplete') == 'one-time-code'):
                    return {'needs_login': True}
                if kind == 'click':
                    await target.click()
                elif kind == 'fill':
                    await target.fill(step['text'])
                else:
                    if step['key'] not in {'Enter', 'Tab', 'Escape', 'ArrowDown', 'ArrowUp'}:
                        raise ValueError('unsupported key')
                    await target.press(step['key'])
            elif kind != 'read':
                raise ValueError('unsupported browser step')
            await self.page.wait_for_load_state('networkidle', timeout=10000)
            self.completed_steps += 1
        if await self.challenged():
            return {'needs_login': True}
        state = await self.context.storage_state(indexed_db=True)
        text = (await self.page.locator('body').inner_text())[:24000]
        # Keep old values too: a site may rotate a cookie while reflecting the old one.
        self.remember(state)
        for secret in sorted(self.held, key=len, reverse=True):
            if secret:
                text = text.replace(secret, '[private]')
        return {'untrusted': True, 'text': text, 'state': state}

    async def run(self):
        from playwright.async_api import async_playwright

        reader = asyncio.create_task(self.receive())
        try:
            async with async_playwright() as playwright:
                while (command := await self.queue.get()) is not None:
                    try:
                        result = await self.command(command, playwright)
                    except Exception as exc:  # noqa: BLE001 - no traces/URLs/values leave cell
                        # Read-only frames can race a redirect's renderer replacement.
                        # Refresh the view; never replay input or agent actions.
                        navigating = command.get('action') == 'frame' and any(
                            phrase in str(exc) for phrase in (
                                'Execution context was destroyed',
                                'Cannot find context with specified id',
                                'Unable to adopt element handle from a different document',
                                'JSHandles can be evaluated only in the context',
                            ))
                        if navigating:
                            self.field = None
                            result = {'navigating': True}
                        else:
                            result = ({'needs_login': True} if self.needs_owner else
                                      {'error': 'browser operation failed; outcome may be unknown'})
                    if command.get('action') == 'steps':
                        result['completed_steps'] = self.completed_steps
                        if result.get('error') or result.get('needs_login'):
                            result['replay'] = 'Do not replay prior steps; reconcile outcomes.'
                    emit({'result': result})
        finally:
            reader.cancel()


def main():
    asyncio.run(Browser().run())
