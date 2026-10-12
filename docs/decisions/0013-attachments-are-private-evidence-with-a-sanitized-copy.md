# ADR-0013: Attachments are private evidence; a text file also has a sanitized copy

- Status: Proposed
- Date: 2026-10-12
- Deciders: Ahmed (to accept by review)

## Context

People attach screenshots, logs and archives to feature requests and issue reports. Some of it holds secrets, some is not what
its name says, and some is built to hurt the server that reads it. The same files later go into an engineering handoff that is
given to Claude Code and, optionally, to an external issue tracker.

## Problem

1. A file must be shown to the people who may see its submission and to nobody else, and a download must not be a way to run
   something in a browser.
2. Evidence has to stay evidence: if the stored file is the masked one, a maintainer can no longer see what was really sent;
   if it is the raw one, everything that reads it risks leaking.
3. The text of an upload is searched with patterns by the server. Patterns that are quadratic on some inputs turn a 3 MB file
   into an outage.
4. "Scanned" must never be said of a file that no scanner looked at.

## Decision

* **The type is decided by the content** (signature, valid UTF-8 without binary bytes, an archive directory), from a fixed list
  an owner can narrow but not widen. `.html`, `.svg`, programs and scripts cannot be attached. A name is a label, reduced to
  a safe last component; it is never a path.
* **Archives are read, never extracted.** The directory is checked for entry count, declared expansion, ratio, absolute and
  climbing paths, links, encryption, programs and nested archives.
* **The bytes are restricted artifacts** under their hash, behind the submission's visibility rule, with every download audited
  and sent as an opaque attachment with `nosniff` and a sandboxing CSP.
* **The original is kept; a text file also gets a sanitized copy** made by the DiagnosticRedactor, with a record of what was
  masked and where and never the value. The handoff will use the sanitized copy; images and archives, which cannot be masked,
  are excluded from it unless a maintainer chooses them.
* **A second inspection** decides `needs_review`: what it still finds is masked and the file is marked as not to be shared
  outside AgentLab until a person has looked.
* **Scanning is optional and reported as it was**: `clean`, `infected` (not kept), `error`, or `not_scanned`.
* **Every pattern is linear**, held by a test against adversarial input, and the heavy work runs outside the database
  transaction. The base redactor's `private_key` and `connection_string` patterns were changed for it.
* **`ArtifactStore.delete(artifact_id, kind=)`** is an optional hook (default: keep and say so), so removing an attachment can
  delete bytes that nothing else uses.

## Alternatives considered

- **Store only the sanitized text.** Rejected: evidence is lost and a redaction mistake is permanent.
- **Store everything unmasked and mask on export.** Rejected: the original and the copy would then be one object, and every
  reader would have to remember to mask.
- **Render previews of images and PDFs on the server.** Rejected: that is parsing hostile files; the interface downloads an
  image and shows it in the browser's own image decoder.
- **Run ClamAV in the stack.** Not now: it needs a daemon and a signature feed; the command hook lets an owner use one.

## Consequences

An attachment is as safe as the restricted store, the visibility rule and the checks make it; masking is best effort and
images can show anything. A large text file (above `sanitize_max_mb`) is kept without a sanitized copy and so is not offered
to the handoff. Migration `0004` adds one table; its downgrade drops it, and the files in the store stay until removed.
