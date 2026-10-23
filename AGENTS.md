# Working on shopping-pantry (for AI agents)

This repository was written by an AI agent and is maintained the same way. These are the
rules that keep it working. Read them before you change anything.

## Layout

- `server/shopping_pantry.py`: the whole server in one file, **Python standard library only**.
  No pip packages, no framework, on purpose: anybody can run it with the Python their system
  already has, and an agent can read all of it at once. Keep it that way.
- `app/index.html`: the whole front end: markup, CSS, inline JavaScript. No build step, no
  framework, no CDN. `app/i18n/*.json`: every word on screen.
- `examples/shopping.example.json`: the configuration, every block optional except the core.
- `deploy/`: systemd unit, Caddy snippet. `tools/check-app.py`: front-end check.

## Rules

0. **Look and feel follow [docs/app-guide.md](docs/app-guide.md)**, shared with inventree-pantry
   and any later household app. A pattern not in the guide that the household asks for goes into
   the guide too.

1. **Every optional part stays optional.** AI, dictation and the pantry bridge are each
   switched on by their config block and must leave no trace when it is missing: no button,
   no error, no request. The server reports them in `/api/config` → `features`; the app
   reads that and nothing else.
2. **No text in the code.** Anything a person reads goes through `t("area.key")`, and the key
   goes into **all** `app/i18n/*.json`, English as the reference. Write missing translations
   yourself and say they are machine-made. Errors from the server are English and shown as
   they are; they are for diagnosis, not for daily use.
3. **Run `python3 tools/check-app.py` after every edit of `app/`.** It parses the script and
   compares the keys in use with every language file, in both directions.
4. **Do not shadow `t`.** It is the translation function; name loop variables `tr`, `item`, ….
5. **Every button that waits for the server goes through `withBusy()`.**
6. **The pantry bridge follows inventree-pantry's rules**, so both apps leave the same data
   behind. The rules are written in the `Pantry` class docstring; when inventree-pantry
   changes them, change them here too. InvenTree is the source of truth for stock: this app
   stores only *that* it booked something (`items.pantry`), never stock itself.
7. **Measured before assumed.** Comments cite what was verified and when. Keep them honest.
8. **Never book test stock into a real pantry** without the household's consent.
9. **Every AI action has an inverse.** `App.execute` records one per action in `ops`, and
   `post_undo` applies them in reverse. A new action type needs its inverse in the same change;
   the only exception is booking into the pantry (stock may move the next minute), and the
   reply says so. The model may only refer to item and list numbers from the state it was
   given; `execute` checks every one.
10. **Bump `VERSION`** in `server/shopping_pantry.py` and add a `CHANGELOG.md` entry for
   anything user-visible. The database schema is upgraded in `Store.__init__`; add a step there
   for every new column, never require a fresh database.

## Commits

This repository is public. Commit as `setuun <6574023+setuun@users.noreply.github.com>`,
never with a personal address. The setting lives in `.git/config`; on a fresh clone run once:

    git config user.name "setuun"
    git config user.email "6574023+setuun@users.noreply.github.com"

## Checks

```sh
python3 tools/check-app.py
python3 -m py_compile server/shopping_pantry.py tools/*.py
python3 server/shopping_pantry.py --config <file> --check
```
