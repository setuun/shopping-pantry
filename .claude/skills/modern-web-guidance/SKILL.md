---
name: modern-web-guidance
description: |
  Look up how to build a web UI pattern with today's browser features (Google Chrome's
  "Modern Web Guidance", pinned 1.0.1). Use at the START of any change to app/index.html:
  dialogs/sheets/menus/popovers, forms and validation, back navigation, toasts, animations,
  dark mode, accessibility, INP/performance, dates. Not for the Python server, i18n files or deploy/.
---

# Modern Web Guidance (pinned, no telemetry)

Adapted from Google Chrome's skill of the same name (Apache-2.0,
https://github.com/GoogleChrome/modern-web-guidance-src), version **1.0.1**, skill version
`2026_11_10-47b4c80e`. Two changes against upstream, both on purpose:

- **Pinned**, not `@latest`: upstream runs whatever npm serves at that moment. To update, change
  the version in every command below after reading the release notes.
- **`DISABLE_TELEMETRY=1`**: upstream sends every search query and guide id to Google by default.

## Which guide wins

- **How something looks and behaves** (colours, layout, which pattern for which need, back
  button, undo): [docs/app-guide.md](../../../docs/app-guide.md). The guide is the household's
  decision and overrides anything here.
- **How it is built** (which HTML element, CSS feature, JS API, which fallback): this skill.
  Prefer a native feature over hand-written JS when the guide's behaviour can be kept.
- The project rules in AGENTS.md (no build, no framework, no CDN, `t()` for every text,
  `tools/check-app.py`) always apply.

**Browser support:** Baseline 2024. A feature whose "Baseline since" year is 2024 or earlier is
used without fallback. Newer features only behind feature detection, with the old behaviour as
the fallback, and only if the fallback costs at most ~20 lines and no dependency.

## Workflow

1. **Search** with a 2–5 word English query for ONE capability (`"light dismiss dialog"`,
   `"swipe to delete row"`, not `"shopping list sheet"`). Translate intent into the UI primitive
   ("confirm before delete" → `"confirm dialog"`). Split compound tasks into several searches.

   ```sh
   DISABLE_TELEMETRY=1 npx -y modern-web-guidance@1.0.1 search "<query>" --skill-version 2026_11_10-47b4c80e
   ```

   Returns `[{id, description}]`. Weak results: rephrase once with the concrete element name
   (`dialog`, `popover`, `form`); then `list` once; if still nothing fits, go on without.

2. **Retrieve** the matching guides plus any broad one that applies (`html`, `css`, `forms`,
   `accessibility`, `performance`); several ids comma-separated:

   ```sh
   DISABLE_TELEMETRY=1 npx -y modern-web-guidance@1.0.1 retrieve "<id>[,<id>…]"
   ```

   Long output is saved to a temp file; read that file in full.

3. **Check** the change against the retrieved guide and against docs/app-guide.md before
   calling it done; then `python3 tools/check-app.py`.

4. **Say which guides shaped the code**: end the answer with a `### Modern Web Guidance` section,
   one bullet per applied guide id with a short verbatim quote of the rule it applied. If guides
   were checked but none applied, one sentence saying so.

Broad modernisation scan (only when asked for an audit of the whole app):

```sh
DISABLE_TELEMETRY=1 npx -y modern-web-guidance@1.0.1 legacy-scan app --json
```

Check each finding against its `keepWhen`/`dropWhen` before reporting it.
