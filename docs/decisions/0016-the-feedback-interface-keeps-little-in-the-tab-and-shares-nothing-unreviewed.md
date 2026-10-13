# ADR-0016: The feedback interface keeps little in the tab, and shares nothing the person has not reviewed

- Status: Proposed
- Date: 2026-10-13
- Deciders: Ahmed (to accept by review)

## Context

The web pages for Feedback & Support sit in front of everything the earlier slices built: submissions, attachments,
diagnostics and the handoff. They are the only part that runs in a stranger's browser, and the part that sees what the person
sees: the form they are typing, what their tab logged to the console, which request failed. The requirements ask for drafts that
survive a reload "where safe", for confirmation before an unsaved form is abandoned, for the browser's own errors as an optional
diagnostic, for upload progress, and for clear warnings before any diagnostic data is shared.

## Problem

1. A draft is useful and is also a copy of what a person typed, which may include a secret they pasted by mistake.
2. The browser's console and failed requests can hold a token, a path, a query string; they are collected by code the person
   never sees.
3. A "Report an issue" link carries context in its address, which anyone can write.
4. `fetch` cannot report how much of an upload has gone; `XMLHttpRequest` can.
5. The application uses a hash router, which has no way to refuse the browser's back button.
6. What a person or a system wrote must never become markup or be mistaken for the page's own words.

## Decision

* **Drafts live in the tab, as text, and go with it.** Session storage (gone when the tab closes), form text only (never a file,
  a diagnostic or a token), offered back with a visible "start again", removed when the submission is saved, when the person
  leaves on purpose and when they sign out.
* **What the tab saw is a bounded buffer in memory.** The last 50 console errors and warnings and the last 50 failed requests
  (method, path without its query, status, the server's request id, duration; never a body), forgotten at sign-out, sent only if
  the person ticks the browser category, sees it masked in the preview and agrees. The server masks it again.
* **Nothing is attached on the person's behalf.** No diagnostic category is ticked; "select all" still requires collecting and
  reviewing; a report with diagnostics chosen and not reviewed is not sent; a failure to collect or attach never stops the report,
  and the confirmation says which part was not attached.
* **Context in a link is identifiers, checked like typed input.** The form lists what will be sent and each can be removed; a
  value that is not an identifier is dropped.
* **Progress only where it is asked for.** Uploads use `fetch`; an upload that wants progress uses `XMLHttpRequest`, with the same
  headers and CSRF token, and reports failures the same way.
* **Ask where the platform lets us.** Closing the tab asks through `beforeunload`; a link inside the interface and *Cancel* ask
  through a capture-phase click handler and a dialog. The back button cannot be asked, and the draft is why nothing is lost.
* **Text is text.** Every user-written value is rendered as a text node in a set-apart block; nothing is `dangerouslySetInnerHTML`
  or Markdown. An internal note is marked in words and colour, and the author is told who will see it.

## Alternatives considered

- **Keep drafts in `localStorage`.** Survives the tab and the browser; also survives a shared computer. Rejected.
- **Switch to a data router for `useBlocker`.** A larger change to the whole interface for one guard; the draft covers the loss.
- **Pre-tick the diagnostics a report usually needs.** Faster, and it is exactly the "dump everything" the requirement forbids.
- **Collect the browser's errors on the server.** The server cannot see them.
- **A progress bar from the proportion of files done.** Honest, but for one large file it shows nothing for a long time.

## Consequences

The tab holds, at most, what the person typed and 100 short records; both are visible to scripts of this origin, which the
content security policy limits to the application's own. A person who closes the tab loses the draft, by design. A hash router
cannot ask about the back button. The interface depends on `XMLHttpRequest` for upload progress and falls back to `fetch` without it.

## Risks

- A secret typed into a draft is in session storage until the tab closes; the form says a draft is kept.
- A console message can hold something sensitive; the person reviews the masked preview before it is shared.
- The click guard follows the markup of links (`#/…`); a link written another way would not be asked about.

## Migration / rollout notes

No migration. One field group is added to `GET /submissions/options` (the attachment limits), so the interface can tell a person
what a file may be before it is sent; the server still decides.
