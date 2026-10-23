# Installing shopping-pantry (instructions for an AI agent)

You are an AI agent setting up shopping-pantry for a household. Work through the steps in
order, run every check, and **stop and ask the person** wherever this guide says *Ask*. Do not
guess answers to those questions; they decide what the household gets.

## 0. Ask first

Ask the person, and write the answers down before you change anything:

1. **Which machine runs it?** Any Linux machine on the home network with Python 3.11 or newer
   (`python3 --version`). It needs a few MB of memory and no other packages. If the household
   runs [inventree-pantry](https://github.com/setuun/inventree-pantry), the InvenTree machine
   is a good place: the pantry bridge then talks to InvenTree locally.
2. **Under which address will phones open it?** It must be **HTTPS** if dictation is wanted
   (browsers allow the microphone only on secure origins), behind a reverse proxy the household
   already has (Caddy, nginx, Traefik), e.g. `https://shopping.home.lan`. *Ask* which proxy and
   which name.
3. **AI?** Is there a language model they want to use: a local server (llama.cpp, Ollama, vLLM,
   LM Studio) or a hosted one (OpenAI, Anthropic, others with an OpenAI-compatible API)? A
   hosted one means the shopping notes leave the house; say so. Without an AI the app still
   works: every comma or line becomes one item.
4. **Dictation?** Is there a speech-to-text service with an OpenAI-compatible
   `/v1/audio/transcriptions` endpoint (whisper.cpp server, faster-whisper-server, OpenAI)?
   Without one the microphone button is simply not shown.
5. **Pantry bridge?** Only if inventree-pantry is installed: the InvenTree address as seen
   from this machine, a user the app may act as (it searches parts, reads stock, books stock
   in), and the location new stock goes to (the pantry's `app.book_location` in `pantry.json`,
   e.g. `Keller/Krisenvorrat`) and its pack-content parameter name (`pack_parameter`).
6. **Language** of the app and the AI's answers: `de` or `en` (more can be added, see AGENTS.md).

## 1. Get the code

```sh
sudo git clone https://github.com/setuun/shopping-pantry /opt/shopping-pantry
cd /opt/shopping-pantry && git checkout <latest tag>     # list tags: git tag --sort=-v:refname
```

## 2. Write the configuration

Copy `examples/shopping.example.json` to `/etc/shopping-pantry/shopping.json` and edit it:

- `listen`: `127.0.0.1:<port>` when the proxy runs on the same machine, else the LAN address.
  Pick a free port (`ss -ltn`).
- `database`: `/var/lib/shopping-pantry/shopping.db` (matches the systemd unit).
- `language`: from question 6. `sections` may be added to change the store sections the AI
  sorts into (a list of names in walking order; the last one is the catch-all).
- **Delete** the `ai`, `speech` or `pantry` block entirely when that part is not wanted.
- `ai`: `api` is `openai` or `anthropic`. `url` is the base URL **including `/v1`** for
  OpenAI-compatible servers (`http://127.0.0.1:11434/v1` for Ollama), and without it for
  Anthropic (`https://api.anthropic.com`). `model` as the server lists it (`GET <url>/models`).
  `extra` is merged into every request; for a reasoning model, switch thinking off there
  (llama.cpp/Qwen: `{"chat_template_kwargs": {"enable_thinking": false}}`) — a list does not
  need it and it costs seconds.
- `speech`: `url` is the full transcription URL; `tts_url`, optional, an OpenAI-compatible
  `/v1/audio/speech` URL for reading replies aloud (each device switches it on in ⋯). Credentials: `user` + `password` for Basic
  auth, or `api_key` for a bearer token.
- `pantry`: `inventree_url` without `/api`; `user` + `password` (the server trades them for an
  API token named `shopping-pantry` on first use) or a fixed `token`.

**Secrets do not belong in the JSON file.** Leave them empty there and put them into
`/etc/shopping-pantry/secrets.env` (mode 0600, owned by root):

```
SHOPPING_AI_API_KEY=...
SHOPPING_SPEECH_PASSWORD=...
SHOPPING_PANTRY_PASSWORD=...
```

## 3. Check before you start it

```sh
sudo env $(sudo cat /etc/shopping-pantry/secrets.env | xargs) \
  python3 /opt/shopping-pantry/server/shopping_pantry.py --config /etc/shopping-pantry/shopping.json --check
```

Every configured part must say `ok`. `pantry: ... book into None` means `book_location` matches
no location: compare with the location paths InvenTree shows. Fix and re-run until it is clean.
The check does not touch the database.

## 4. Run it as a service

```sh
sudo cp /opt/shopping-pantry/deploy/shopping-pantry.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now shopping-pantry
journalctl -u shopping-pantry -n 20
curl -s http://<listen>/api/config
```

The log's first line lists which parts are on. `/api/config` answers with `features`.

## 5. Put it behind the proxy

Add a site for the address from question 2 that proxies to `listen`. For Caddy see
`deploy/Caddyfile.snippet`. Server-Sent Events (`/api/events`) must not be buffered: Caddy
needs nothing; nginx needs `proxy_buffering off;` for that path. Make sure the name resolves on
the home network (and over the VPN, if the household uses one).

## 6. Verify like a user

Open the address on a phone (or with a phone-sized browser window):

1. The app asks for a device name. Give one.
2. Type `milk, bread` and press the button. With AI: a suggestion appears, add it. Without:
   two items appear.
3. Open the same address in a second browser: the items are there. Tick one in the second
   browser; the first shows it ticked within a second or two.
4. With dictation: tap the microphone, say a few words, tap again; the text lands in the field.
5. With the pantry bridge: write an item the pantry has, e.g. a staple — the suggestion shows
   "in the pantry: …". Do **not** book test stock into a real pantry unless the person agrees;
   ask them to try the "Into the pantry?" flow with a real purchase.

Report to the person what works, what you skipped and why.

## Updating

```sh
cd /opt/shopping-pantry && sudo git fetch --tags && sudo git checkout <new tag>
sudo systemctl restart shopping-pantry
```

Read `CHANGELOG.md` between the two tags first. The database is upgraded in place on start.
