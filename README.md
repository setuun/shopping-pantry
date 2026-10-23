# shopping-pantry

A shared shopping list for a household, on the phone. Say or type a few words — *"milk,
something for dinner, washing-up liquid, wood glue"* — and an AI turns them into a sorted list
with amounts. Everyone on the home network sees the same list, live, and ticks things off in
the shop.

It is the sibling of [inventree-pantry](https://github.com/setuun/inventree-pantry), and the
two work together when both are there: the list knows what is already in the pantry, can fill
itself from what the pantry is missing, and books what you bought into the pantry — only the
part that goes there.

Deutsch: [README.de.md](README.de.md)

<p>
  <img src="docs/screenshots/list-de.png" width="200" alt="A list, sorted by store section">
  <img src="docs/screenshots/command-de.png" width="200" alt="A typed command and the AI's reply with undo">
  <img src="docs/screenshots/partial-de.png" width="200" alt="Only part bought: how many did you get?">
  <img src="docs/screenshots/lists-de.png" width="200" alt="Several lists">
</p>

> [!IMPORTANT]
> **This app is vibe-coded.** It was written almost entirely by an AI coding agent (Claude
> Code), directed by one person, for one household. Every change was checked against a real
> setup, but no human has reviewed the code line by line. Read before you run it.
>
> **The installation guide is written for an AI agent to carry out.** Point your agent
> (Claude Code, Codex, Gemini CLI, …) at [INSTALL.md](INSTALL.md) and let it do the work. It
> checks its own steps and stops to ask you where a decision is yours.

## What it does

- **Lists for everything.** The weekly shop, the chemist, the DIY store: several lists side
  by side, not only food.
- **Only got part of it?** Tap the amount on the right ("3 packs"), say how many you got; the
  rest stays on the list.
- **Live on every phone.** Tick an item in the shop and it is ticked at home a moment later.
  No login: the network is the boundary. Each device names itself once, so the list shows who
  added or ticked what.
- **AI, optional: say what to do.** One line at the bottom, typed or spoken, runs the whole
  app: *"milk and eggs"*, *"got the bread and the butter"*, *"the screws go on the DIY list"*,
  *"make it 3 litres"*, *"new list for the barbecue on Saturday with sausages and charcoal"*,
  *"ingredients for lasagne for four"*, *"the usual"*, *"what did I forget?"*. It runs at once
  and answers in one line with an **undo**; when a command is ambiguous it asks back instead
  of guessing. Replies can be read aloud. Any OpenAI-compatible server works (llama.cpp,
  Ollama, vLLM, LM Studio, OpenAI), or Anthropic.
- **Dictation, optional.** A round microphone button next to the command line; it stops by
  itself after ten seconds of silence. The recording goes to
  any OpenAI-compatible transcription endpoint (whisper.cpp, faster-whisper, OpenAI) through
  the server, so phones never see its address or password.
- **Without either, it is a plain shared list:** every comma or line becomes one item.

## Together with inventree-pantry (optional)

The pantry app keeps the household's stock in [InvenTree](https://inventree.org). Its own
shopping list is about the pantry only. This app is the everyday list, and builds a bridge:

- **Ask it:** *"do we still have rice?"* is answered from InvenTree, and a recipe leaves out
  what the pantry holds.
- **While writing the list:** the reply says what the pantry already holds — *"in the
  pantry: 9 l almond drink"* — so "buy it anyway?" is a glance. Nothing is written.
- **"Missing from the pantry"** adds what InvenTree reports below its target, as a suggestion
  to tick.
- **After shopping:** say *"6 almond milk into the pantry"*, or tap *"Into the pantry?"* on a
  ticked item. The AI already pointed at the
  matching article. You say how many of what you bought go into the pantry — *10 bought, 6
  into the pantry, 4 stay in the kitchen* — and only those are booked, with location and
  best-before date. Hardware or tomorrow's bread is just ticked off.

Booking follows the pantry app's own rules, so both apps leave the same data behind: stock
goes onto products, never onto requirements; measured articles are booked as packs × pack
content; a new batch is merged into an existing one only when the best-before date matches.

## How it is built

One Python file with the standard library only — no pip, no build, no framework — and one
HTML file for the phone. SQLite for the lists, Server-Sent Events for the live updates. It
runs wherever Python 3.11 runs and needs a few megabytes of memory.

```
server/shopping_pantry.py   the server: API, live updates, AI, dictation, pantry bridge
app/index.html              the whole front end; texts in app/i18n/*.json
examples/                   a commented configuration
deploy/                     systemd unit, Caddy snippet
tools/check-app.py          checks the front end after every change
```

Configuration is one JSON file; every optional part is a block that can be left out. See
[examples/shopping.example.json](examples/shopping.example.json) and [INSTALL.md](INSTALL.md).

```sh
python3 server/shopping_pantry.py --config shopping.json          # run
python3 server/shopping_pantry.py --config shopping.json --check  # test the configured services
```

## Connecting an AI (and the other optional parts)

Like the photo recognition of inventree-pantry, the AI is whatever the household runs, and the
app holds no account of its own: you give it an address, a model and, for a hosted service, a key.

```json
"ai": {
  "api": "openai",
  "url": "http://127.0.0.1:11434/v1",
  "model": "qwen3:8b",
  "api_key": "",
  "timeout": 60,
  "extra": {}
}
```

| Key | Meaning |
|---|---|
| `api` | wire format: `openai` (OpenAI, llama.cpp, Ollama's `/v1`, vLLM, LM Studio, most hosted services) or `anthropic` |
| `url` | base URL; with `/v1` for OpenAI-compatible servers |
| `model` | the model name as the server lists it |
| `api_key` | **leave it empty here** and put the key into the environment as `SHOPPING_AI_API_KEY` (see `deploy/shopping-pantry.service`, which reads `/etc/shopping-pantry/secrets.env`); empty for a local server that needs none |
| `extra` | merged into every request, for server specifics (e.g. switching off a reasoning model's thinking) |

The same goes for dictation (`speech`: `SHOPPING_SPEECH_PASSWORD`) and the pantry bridge
(`pantry`: `SHOPPING_PANTRY_PASSWORD` or `SHOPPING_PANTRY_TOKEN`). Leave a block out and that part
is simply not there. [INSTALL.md](INSTALL.md) walks through all of it.

## Security

There is no login, on purpose: the app is meant for a home network (and a VPN into it), like
a list on the fridge door. Do not expose it to the internet. The secrets for the AI, the
speech service and InvenTree stay on the server.

## License

MIT, see [LICENSE](LICENSE).
