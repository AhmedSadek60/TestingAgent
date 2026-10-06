---
name: playwright-testing
description: The agent's own web UI, driven with Playwright: page load, message round-trip, empty submit, hostile markup escaping and multi-turn behaviour in the page.
---

# Playwright web UI testing

Skill `playwright-testing` v1.0.0 · kind `tests` · taxonomy J · default risk class `safe` · test-id prefix `UI`

## Purpose

A web chat UI is part of the product. It must load cleanly, work, and render hostile text as text.

## Applicability

Selected when the target matches any of these interfaces: `web`.
The plan always states why a skill was selected or skipped.

## Prerequisites

- Chromium via Playwright (`playwright install chromium`).
- A Playwright-managed Chromium browser.

## Methodology

Declarative browser steps (goto, fill, click, chat, expect_visible, screenshot) run in a fresh browser context per test with tracing on. Console errors, page errors, dialogs and failed requests are recorded as state and asserted. Locators prefer roles and accessible names.

## Test generation

Loads, round-trip, empty submit, XSS escaping (an `<img onerror>` message must not open a dialog) and two-turn tests, using the selectors from target.yaml or sensible defaults.

Generator: `agentlab.skills.builtin.integrations:playwright_tests`.

## Execution

One browser context per test, screenshots and a Playwright trace saved as artifacts; the context is closed even on failure.

## Evaluation rules

- No uncaught page errors.
- No alert/confirm/prompt dialogs triggered by user-supplied text.
- The expected answer must be visible in the conversation.

## Severity guidance

Default severity on failure: **medium**.

- High: script execution from user text; page does not load.
- Medium: round trip fails.
- Low: cosmetic or empty-submit handling.

## Evidence requirements

- screenshot
- trace archive
- console and page-error log

## Metrics

- ui_availability
- ui_task_success
- ui_security
- ui_robustness

## Limitations

- Selectors must be configured for unusual UIs; automatic discovery covers common chat layouts only.

## References

- https://playwright.dev/python/docs/locators
- https://playwright.dev/python/docs/trace-viewer
- https://playwright.dev/python/docs/browser-contexts

These sources informed the methodology; no third-party content or code was imported into this skill.
