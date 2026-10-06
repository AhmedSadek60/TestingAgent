"""Looking at a web target's page before testing it (spec sections 5 and 18).

Opens the page in a clean browser context and records what is there: the title, the forms, the inputs and buttons, whether
it looks like a chat page (a message box and a send button), and which front-end framework it appears to use. Nothing is
typed and nothing is clicked. The result becomes part of the target profile (``profile.browser["page"]``). A page that
cannot be opened is reported as unreachable with the reason; discovery never raises.
"""

from __future__ import annotations

from typing import Any

from agentlab.browser.auth import open_context
from agentlab.browser.chat import INPUT_SELECTORS, SEND_SELECTORS, first_visible
from agentlab.browser.pool import BrowserPool
from agentlab.browser.session import BrowserSession
from agentlab.core.models import TargetSpec
from agentlab.security.credentials import CredentialManager

SURVEY = """() => ({
  title: document.title || '',
  forms: document.forms.length,
  links: document.querySelectorAll('a[href]').length,
  inputs: Array.from(document.querySelectorAll('input,textarea,[contenteditable=true]')).slice(0, 20)
    .map(e => ({tag: e.tagName.toLowerCase(), type: e.type || null, name: e.name || e.id || null})),
  buttons: Array.from(document.querySelectorAll('button,input[type=submit]')).slice(0, 20)
    .map(b => (b.innerText || b.value || '').trim().slice(0, 40)),
  frameworks: [
    (window.React || document.querySelector('[data-reactroot]')) ? 'react' : null,
    window.Vue ? 'vue' : null,
    (window.angular || document.querySelector('[ng-version]')) ? 'angular' : null,
    window.__NEXT_DATA__ ? 'nextjs' : null,
    document.querySelector('[data-testid="stApp"]') ? 'streamlit' : null,
    (window.gradio_config || document.querySelector('gradio-app')) ? 'gradio' : null,
  ].filter(Boolean),
})"""


async def discover_web(
    pool: BrowserPool, spec: TargetSpec, credentials: CredentialManager | None = None
) -> dict[str, Any]:
    web = spec.web
    if web is None:
        return {}
    try:
        opened = await open_context(pool, web, credentials)
    except Exception as exc:  # a credential that is missing is reported by the tests that need it
        return {"reachable": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    session: BrowserSession | None = None
    try:
        page = await opened.guarded.context.new_page()
        session = BrowserSession(opened.guarded, page, web=web)
        if opened.login is not None and web.login_url:
            await session.form_login(web.login_url, *opened.login)
        response = await page.goto(web.url, wait_until="load", timeout=15_000)
        survey = await page.evaluate(SURVEY)
        box = await first_visible(page, (web.input_selector,) if web.input_selector else INPUT_SELECTORS)
        send = await first_visible(page, (web.send_selector,) if web.send_selector else SEND_SELECTORS)
        return {
            "reachable": True,
            "status": response.status if response else None,
            "final_url": page.url,
            "chat_ui": box is not None and send is not None,
            **survey,
        }
    except Exception as exc:
        first_line = str(exc).splitlines()[0][:200] if str(exc) else ""
        return {"reachable": False, "error": f"{type(exc).__name__}: {first_line}"}
    finally:
        if session is not None:
            await session.close()
        else:
            await opened.guarded.context.close()
