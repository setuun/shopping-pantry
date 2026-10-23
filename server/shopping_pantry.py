#!/usr/bin/env python3
"""shopping-pantry: a shared shopping list for a household, with an optional AI that turns a
few words into a list, optional dictation, and an optional bridge to inventree-pantry.

One file, Python standard library only, on purpose: no pip, no build, no framework. It runs
wherever Python 3.11+ runs, and an AI agent can read all of it in one go (see AGENTS.md).

    python3 server/shopping_pantry.py --config /etc/shopping-pantry/shopping.json

What it serves:
  /                  the app (app/index.html and friends), static
  /api/...           JSON API, see the ROUTES table at the bottom
  /api/events        Server-Sent Events: every change is pushed to every open phone

Every optional part is switched on by its block in the config file and is simply absent
without it. The list itself needs nothing but this file and a writable directory.
"""

import argparse
import base64
import json
import mimetypes
import os
import re
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "0.7.1"

HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.normpath(os.path.join(HERE, "..", "app"))

# Store sections the AI sorts into, in walking order through a typical supermarket. The config
# can replace them (`sections`); the last one is the catch-all.
DEFAULT_SECTIONS = {
    "de": ["Obst & Gemüse", "Brot & Backwaren", "Kühlregal", "Fleisch & Fisch", "Tiefkühl",
           "Vorrat & Konserven", "Getränke", "Süßes & Snacks", "Drogerie & Haushalt",
           "Baumarkt", "Sonstiges"],
    "en": ["Fruit & vegetables", "Bread & bakery", "Dairy & chilled", "Meat & fish", "Frozen",
           "Pantry & tins", "Drinks", "Sweets & snacks", "Toiletries & household",
           "DIY & hardware", "Other"],
}

MAX_BODY = 25 * 1024 * 1024          # a minute of 16 kHz WAV is ~2 MB; this is generous
SSE_HEARTBEAT = 25                   # seconds; below the usual 60 s proxy idle timeout


def log(*parts):
    print(datetime.now().strftime("%H:%M:%S"), *parts, file=sys.stderr, flush=True)


class Binary:
    """A handler result that is sent as raw bytes (audio) instead of JSON."""

    def __init__(self, data, ctype):
        self.data = data
        self.ctype = ctype


class ApiError(Exception):
    """An error the client is meant to show. `status` becomes the HTTP status."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


# ---- Configuration -------------------------------------------------------------------------

def load_config(path):
    cfg = {}
    if path:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    lang = cfg.get("language") or "de"
    cfg.setdefault("language", lang)
    cfg.setdefault("listen", "127.0.0.1:8093")
    cfg.setdefault("database", os.path.join(os.getcwd(), "shopping.db"))
    cfg.setdefault("sections", DEFAULT_SECTIONS.get(lang, DEFAULT_SECTIONS["en"]))
    # Secrets may come from the environment instead of the file, so the file can be world-
    # readable in a container and the secrets live in the unit's EnvironmentFile.
    for block, key, env in (("ai", "api_key", "SHOPPING_AI_API_KEY"),
                            ("speech", "password", "SHOPPING_SPEECH_PASSWORD"),
                            ("pantry", "password", "SHOPPING_PANTRY_PASSWORD"),
                            ("pantry", "token", "SHOPPING_PANTRY_TOKEN")):
        if cfg.get(block) and os.environ.get(env):
            cfg[block][key] = os.environ[env]
    return cfg


# ---- Database ------------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS lists (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    archived    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS items (
    -- AUTOINCREMENT: a number is never handed out twice. Plain INTEGER PRIMARY KEY reuses the
    -- highest one after a delete, and undo restores rows by number (seen live 2026-10-23).
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    list_id     INTEGER NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    quantity    TEXT NOT NULL DEFAULT '',
    section     TEXT NOT NULL DEFAULT '',
    note        TEXT NOT NULL DEFAULT '',
    done        INTEGER NOT NULL DEFAULT 0,
    created_by  TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    done_by     TEXT NOT NULL DEFAULT '',
    done_at     TEXT,
    -- the pantry bridge: NULL = not decided yet, otherwise JSON
    -- {"state": "booked"|"skipped", "part": pk, "part_name": ..., "amount": ..., "units": ...}
    pantry      TEXT,
    -- the pantry article the AI's search pointed at, offered first when booking in
    pantry_hint INTEGER,
    -- "remove done items" hides them instead of deleting: what was bought, and when, is
    -- what "the usual" and "what did I forget?" are made of
    cleared     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS items_list ON items(list_id);
-- One row per AI command: the inverse operations, applied in reverse order by "undo".
CREATE TABLE IF NOT EXISTS undo (
    id          INTEGER PRIMARY KEY,
    created_at  TEXT NOT NULL,
    ops         TEXT NOT NULL
);
"""


class Store:
    """SQLite behind one lock. A household writes a few times a minute; one connection and a
    lock are simpler than a pool and cannot deadlock."""

    def __init__(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.executescript(SCHEMA)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(items)")}
        if "pantry_hint" not in cols:                      # databases from before 0.1.0
            self.db.execute("ALTER TABLE items ADD COLUMN pantry_hint INTEGER")
        if "cleared" not in cols:                          # databases from before 0.2.0
            self.db.execute("ALTER TABLE items ADD COLUMN cleared INTEGER NOT NULL DEFAULT 0")
        sql = self.db.execute("SELECT sql FROM sqlite_master WHERE name = 'items'").fetchone()[0]
        if "AUTOINCREMENT" not in sql:                     # databases from before 0.3.1
            self.rebuild_items()
        self.lock = threading.Lock()
        if not self.query("SELECT 1 FROM lists LIMIT 1"):
            self.execute("INSERT INTO lists (name, created_at) VALUES (?, ?)",
                         ("", now_iso()))

    def rebuild_items(self):
        """Copy items into a table with AUTOINCREMENT, keeping every number. One transaction:
        either the new table is complete or the old one is untouched."""
        cols = [r[1] for r in self.db.execute("PRAGMA table_info(items)")]
        create = SCHEMA[SCHEMA.index("CREATE TABLE IF NOT EXISTS items"):]
        create = create[:create.index(");") + 2].replace(
            "CREATE TABLE IF NOT EXISTS items", "CREATE TABLE items_new")
        self.db.execute("PRAGMA foreign_keys = OFF")
        self.db.execute("BEGIN")
        try:
            self.db.execute(create)
            self.db.execute("INSERT INTO items_new (%s) SELECT %s FROM items"
                            % (", ".join(cols), ", ".join(cols)))
            self.db.execute("DROP TABLE items")
            self.db.execute("ALTER TABLE items_new RENAME TO items")
            self.db.execute("CREATE INDEX IF NOT EXISTS items_list ON items(list_id)")
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        finally:
            self.db.execute("PRAGMA foreign_keys = ON")
        log("database: items now numbered with AUTOINCREMENT")

    def query(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def execute(self, sql, args=()):
        with self.lock:
            cur = self.db.execute(sql, args)
            return cur.lastrowid, cur.rowcount


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_qty(text):
    """ "3 Päckchen" -> (3.0, "Päckchen"), "500g" -> (500.0, "g"), "etwas" -> None."""
    m = re.match(r"^\s*(\d+(?:[.,]\d+)?)\s*(.*?)\s*$", text or "")
    return (float(m.group(1).replace(",", ".")), m.group(2)) if m else None


def fmt_qty(n, unit, language):
    num = ("%d" % n) if n == int(n) else ("%g" % n)
    if language == "de":
        num = num.replace(".", ",")
    return (num + " " + unit).strip()


def item_out(row):
    row = dict(row)
    row["done"] = bool(row["done"])
    row["pantry"] = json.loads(row["pantry"]) if row.get("pantry") else None
    return row


# ---- Live updates --------------------------------------------------------------------------

class Hub:
    """Fan-out for Server-Sent Events. A change bumps a counter and wakes every waiting stream;
    the streams send only "list N changed", and the phones fetch the list again. Sending diffs
    would be faster and is not worth it for lists of thirty lines."""

    def __init__(self):
        self.cond = threading.Condition()
        self.seq = 0
        self.last = []                       # (seq, payload), the most recent few

    def publish(self, payload):
        with self.cond:
            self.seq += 1
            self.last = (self.last + [(self.seq, payload)])[-50:]
            self.cond.notify_all()

    def wait(self, after, timeout):
        with self.cond:
            self.cond.wait_for(lambda: self.seq > after, timeout=timeout)
            return self.seq, [p for s, p in self.last if s > after]


# ---- Outgoing HTTP -------------------------------------------------------------------------

def http_json(url, method="GET", body=None, headers=None, timeout=30):
    data = None
    hdrs = {"Accept": "application/json"}
    hdrs.update(headers or {})
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        detail = e.read()[:500].decode(errors="replace")
        raise ApiError("%s %s -> HTTP %s: %s" % (method, url.split("?")[0], e.code, detail), 502)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ApiError("%s %s: %s" % (method, url.split("?")[0], e), 502)
    return json.loads(raw) if raw else None


def multipart(fields, files):
    """Encode a multipart/form-data body. `files` = [(name, filename, ctype, bytes)]."""
    boundary = uuid.uuid4().hex
    out = []
    for k, v in fields.items():
        out.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                    % (boundary, k, v)).encode())
    for name, filename, ctype, data in files:
        out.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\n"
                    "Content-Type: %s\r\n\r\n" % (boundary, name, filename, ctype)).encode())
        out.append(data)
        out.append(b"\r\n")
    out.append(("--%s--\r\n" % boundary).encode())
    return b"".join(out), "multipart/form-data; boundary=" + boundary


# ---- AI: a sentence -> actions on the app --------------------------------------------------

COMMAND_PROMPT = """You run a household's shared shopping list app. People speak or type short
commands, often while shopping with one hand: "milk and eggs", "got the bread and the butter",
"the screws go on the DIY list", "make it 3 litres of milk", "ingredients for lasagne for 4",
"do we still have rice?", "6 almond milk into the pantry, the rest stays in the kitchen".

You get the app's current state, then the command. Answer with ONE JSON object and nothing else.

Either ask the server for pantry facts first (only when the pantry is connected and you need
stock to decide, e.g. a question about stock, or a recipe where some ingredients may be stored):
  {{"lookups": ["Reis", "Passata", "Hackfleisch"]}}
You then get the results and answer again. At most two lookup rounds.

Or act:
  {{"actions": [...], "reply": "...", "question": false}}

Actions (refer to items and lists by their number from the state):
  {{"type": "add", "list": 1, "name": "...", "quantity": "", "section": "...", "note": "", "search": ["..."]}}
  {{"type": "check", "item": 12}}             ticked off / bought / "have it"
  {{"type": "uncheck", "item": 12}}
  {{"type": "partial", "item": 12, "got": "2 Päckchen", "rest": "1 Päckchen"}}   only part was bought:
                                              ticks the item with what was got, the rest stays open
  {{"type": "update", "item": 12, "name": "...", "quantity": "...", "section": "...", "note": "..."}}  (only fields that change)
  {{"type": "remove", "item": 12}}            deleted, not bought
  {{"type": "move", "item": 12, "list": 3}}
  {{"type": "create_list", "ref": "new1", "name": "..."}}   later actions may use "list": "new1"
  {{"type": "rename_list", "list": 3, "name": "..."}}
  {{"type": "clear_done", "list": 1}}
  {{"type": "show_list", "list": 3}}          switch the person's screen to that list
{pantry_actions}
Rules:
- "list" defaults to the current list. Never invent item numbers; only use numbers from the state.
- Act on the CURRENT list. Items on other lists are only touched when the person names that list.
- Only touch the items the command is about. "Reopen the carrots" reopens carrots, nothing else.
- Words like "shopping list", "Einkaufsliste", "the list", "auf die Liste" mean the CURRENT list.
  Only use create_list when the person explicitly asks for a new or another list ("new list",
  "neue Liste", "leg eine Liste an").
- A question ("what is on the list?", "do we have rice?") is answered in "reply" and changes
  nothing: no actions.
- The same thing in another spelling, form or language is the same item: Kaffee = Café = coffee,
  Ei = Eier. Do not add it a second time; update the quantity if more is asked for.
- Commands often come from speech recognition. Words may be glued together or misheard:
  "Eiermilch" is "Eier, Milch"; split a word into items when it is not a usual product name.
- If a command is ambiguous (two items could be meant, no list fits), do NOT guess: no actions,
  ask a short question in "reply" and set "question": true. The next command is the answer.
- Add: one item per thing; keep the household's own words; turn vague wishes ("something for
  dinner") into a few concrete items with a note what they are for. "quantity" only when given or
  obvious. "section" must be one of: {sections}. Skip items already open on the list unless more
  is asked for. "search": 1-3 short words to find the item in a household inventory, compound
  words split ("Mandelmilch" -> "Mandel", "Milch"); [] for things nobody stores.
- Recipes: add the ingredients with the dish as note; leave out what the pantry lookups show is
  stored in sufficient amount, and say so in the reply.
- "The usual", "what did I forget?": use the purchase history in the state; suggest by adding
  items that are bought regularly and are due, not ones already on the list.
- "reply": one or two short sentences in the language "{language}", saying what you did or
  answering the question. No lists of everything, no markdown.
"""

PANTRY_ACTIONS = """  {{"type": "book", "item": 12, "packets": 6}}   put bought packets of an item into the pantry
                                              (the rest stays in the kitchen and is not booked)
  {{"type": "pantry_skip", "item": 12}}       this item does not go into the pantry
"""


def parse_json(text):
    """The model's answer -> dict. Tolerates a reasoning preamble and code fences."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ApiError("The AI did not answer with JSON.", 502)
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        raise ApiError("The AI's JSON could not be read.", 502)
    if not isinstance(data, dict):
        raise ApiError("The AI's JSON is not an object.", 502)
    return data


class AI:
    """Chat completion in two wire formats: `openai` (OpenAI, llama.cpp, vLLM, LM Studio,
    Ollama's /v1, most hosted services) and `anthropic`. Server specifics (switching off a
    reasoning model's thinking, say) go into `extra`, which is merged into every request."""

    def __init__(self, cfg):
        self.api = cfg.get("api", "openai")
        self.url = cfg["url"].rstrip("/")
        self.model = cfg.get("model", "")
        self.key = cfg.get("api_key", "")
        self.timeout = cfg.get("timeout", 60)
        self.extra = cfg.get("extra") or {}
        if self.api not in ("openai", "anthropic"):
            raise SystemExit("ai.api must be 'openai' or 'anthropic', not %r" % self.api)

    def chat(self, system, user):
        if self.api == "anthropic":
            body = {"model": self.model, "max_tokens": 2000, "system": system,
                    "messages": [{"role": "user", "content": user}]}
            body.update(self.extra)
            r = http_json(self.url + "/v1/messages", "POST", body,
                          {"x-api-key": self.key, "anthropic-version": "2023-06-01"},
                          self.timeout)
            return "".join(b.get("text", "") for b in r.get("content", []))
        body = {"model": self.model, "temperature": 0.2,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}]}
        body.update(self.extra)
        hdrs = {"Authorization": "Bearer " + self.key} if self.key else {}
        r = http_json(self.url + "/chat/completions", "POST", body, hdrs, self.timeout)
        return r["choices"][0]["message"].get("content") or ""


# ---- Speech: audio -> text -----------------------------------------------------------------

class Speech:
    """Any OpenAI-compatible transcription endpoint (whisper.cpp server, faster-whisper,
    OpenAI itself). The browser never sees its address or password: it posts the recording
    here and this server passes it on."""

    def __init__(self, cfg):
        self.url = cfg["url"]
        # Optional: an OpenAI-compatible /v1/audio/speech endpoint for reading replies aloud.
        self.tts_url = cfg.get("tts_url", "")
        self.tts_voice = cfg.get("tts_voice", "")
        self.language = cfg.get("language", "")
        self.model = cfg.get("model", "")
        self.timeout = cfg.get("timeout", 60)
        self.headers = {}
        if cfg.get("user") or cfg.get("password"):
            pair = "%s:%s" % (cfg.get("user", ""), cfg.get("password", ""))
            self.headers["Authorization"] = "Basic " + base64.b64encode(pair.encode()).decode()
        elif cfg.get("api_key"):
            self.headers["Authorization"] = "Bearer " + cfg["api_key"]

    def transcribe(self, audio, ctype):
        fields = {"response_format": "json"}
        if self.language:
            fields["language"] = self.language
        if self.model:
            fields["model"] = self.model
        ext = "wav" if "wav" in ctype else "webm"
        body, mp = multipart(fields, [("file", "dictation." + ext, ctype, audio)])
        req = urllib.request.Request(self.url, data=body, method="POST",
                                     headers=dict(self.headers, **{"Content-Type": mp}))
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return (json.loads(r.read()).get("text") or "").strip()
        except urllib.error.HTTPError as e:
            raise ApiError("Speech service: HTTP %s" % e.code, 502)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ApiError("Speech service: %s" % e, 502)

    def speak(self, text):
        body = {"input": text, "response_format": "wav"}
        if self.tts_voice:
            body["voice"] = self.tts_voice
        if self.model:
            body["model"] = self.model
        req = urllib.request.Request(self.tts_url, data=json.dumps(body).encode(), method="POST",
                                     headers=dict(self.headers,
                                                  **{"Content-Type": "application/json"}))
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            raise ApiError("Speech service (voice output): HTTP %s" % e.code, 502)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ApiError("Speech service (voice output): %s" % e, 502)


# ---- Pantry bridge (InvenTree, as inventree-pantry uses it) --------------------------------

class Pantry:
    """Reads and books stock in the InvenTree behind inventree-pantry, following that app's
    rules so both apps leave the same data behind:

    * A requirement ("Reis") is a template part; the products bought are its variants. Stock is
      booked onto products only. Units ("kg", "" = pieces) belong to the requirement.
    * Measured articles are bought in packets: amount = packets x pack content, where the pack
      content is a part parameter (name from pantry.json, `pack_parameter`). The base amount is
      stored, because InvenTree compares `low_stock` against exactly that number.
    * A new batch merges into an existing one only when location AND best-before date match;
      adding to a batch with a different date would destroy the older date.
    """

    def __init__(self, cfg):
        self.base = cfg["inventree_url"].rstrip("/") + "/api"
        self.app_url = cfg.get("app_url", "")
        self.book_location = cfg.get("book_location", "")
        self.pack_name = cfg.get("pack_parameter", "Pack content")
        self.timeout = cfg.get("timeout", 20)
        # A fixed token, or user + password, from which a token is minted on first use.
        # Not Basic on every call: InvenTree then runs its password hash for each request,
        # measured 2026-10-23 at ~1 s per search, and parallel searches only queue up behind
        # it. A token check is a database lookup.
        self.token = cfg.get("token") or ""
        pair = "%s:%s" % (cfg.get("user", ""), cfg.get("password", ""))
        self.basic = "Basic " + base64.b64encode(pair.encode()).decode()
        self.can_mint = bool(cfg.get("user"))
        self._token_lock = threading.Lock()
        self._cache = {}
        self._cache_lock = threading.Lock()

    def mint(self):
        """Token for user + password. InvenTree hands out the SAME token again for the same
        name (as inventree-pantry relies on), so restarts do not pile up tokens; it expires
        after a year, and a 401 then simply mints the next one."""
        r = http_json(self.base + "/user/me/token/?name=shopping-pantry", "GET", None,
                      {"Authorization": self.basic}, self.timeout)
        if not r or not r.get("token"):
            raise ApiError("InvenTree gave no token for this user.", 502)
        return r["token"]

    def api(self, path, method="GET", body=None):
        with self._token_lock:
            if not self.token and self.can_mint:
                self.token = self.mint()
            token = self.token
        try:
            return http_json(self.base + path, method, body,
                             {"Authorization": "Token " + token}, self.timeout)
        except ApiError as e:
            if "HTTP 401" not in str(e) or not self.can_mint:
                raise
            with self._token_lock:
                if self.token == token:
                    self.token = self.mint()
                token = self.token
            return http_json(self.base + path, method, body,
                             {"Authorization": "Token " + token}, self.timeout)

    def cached(self, key, ttl, fn):
        with self._cache_lock:
            hit = self._cache.get(key)
            if hit and hit[0] > time.time():
                return hit[1]
        val = fn()
        with self._cache_lock:
            self._cache[key] = (time.time() + ttl, val)
        return val

    def templates(self):
        """pk -> template part, for units and names of requirements."""
        def load():
            rows = self.api("/part/?is_template=true&limit=1000")
            rows = rows.get("results", rows) if isinstance(rows, dict) else rows
            return {p["pk"]: p for p in rows}
        return self.cached("templates", 300, load)

    def pack_template(self):
        def load():
            rows = self.api("/parameter/template/?limit=200")
            rows = rows.get("results", rows) if isinstance(rows, dict) else rows
            return next((t["pk"] for t in rows if t.get("name") == self.pack_name), None)
        return self.cached("pack_template", 3600, load)

    def locations(self):
        def load():
            rows = self.api("/stock/location/?limit=500")
            rows = rows.get("results", rows) if isinstance(rows, dict) else rows
            return [{"pk": l["pk"], "name": l["name"], "path": l.get("pathstring") or l["name"]}
                    for l in rows]
        return self.cached("locations", 300, load)

    def default_location(self):
        for l in self.locations():
            if self.book_location in (l["path"], l["name"]):
                return l["pk"]
        return None

    def units_of(self, part):
        if part.get("variant_of"):
            tpl = self.templates().get(part["variant_of"])
            if tpl:
                return tpl.get("units") or ""
        return part.get("units") or ""

    def brief(self, p):
        return {"pk": p["pk"], "name": p.get("full_name") or p["name"],
                "template": bool(p.get("is_template")),
                "variant_of": p.get("variant_of"),
                "units": self.units_of(p),
                "in_stock": float(p.get("total_in_stock") or p.get("in_stock") or 0),
                "minimum": float(p.get("minimum_stock") or 0)}

    def search(self, term, limit=6):
        term = term.strip()
        if not term:
            return []
        q = urllib.parse.urlencode({"search": term, "active": "true", "limit": limit})
        rows = self.api("/part/?" + q)
        rows = rows.get("results", rows) if isinstance(rows, dict) else rows
        return [self.brief(p) for p in rows]

    def hint(self, name, terms=()):
        """Best pantry match for a list line, or None.

        Searched by the name AND by the AI's search words, because InvenTree's search is a
        substring match: "Mandelmilch" does not find "Milch Mandel ohne Zucker", the words
        "Mandel" and "Milch" do. A hit scores for every word it contains; ties go to the
        requirement (template), whose stock is the total over all its products."""
        words = [w for w in [name] + list(terms) if w]
        found = {}
        try:
            with ThreadPoolExecutor(len(words)) as pool:
                for hits in pool.map(lambda w: self.search(w, 6), words):
                    for h in hits:
                        found[h["pk"]] = h
        except ApiError:
            return None
        if not found:
            return None
        # Whole words only. InvenTree matches inside words, which is right for finding and
        # wrong for claiming: "Brot" found "Knäckebrot", "Butter" found "Butterschmalz"
        # (2026-10-23, live). The full name counts double, each AI search word once; two
        # points are needed, so "Mandelmilch" -> "Milch Mandel ohne Zucker" (Mandel + Milch)
        # and "Zahnpasta" -> "Zahnpasta (Tube)" pass, a single shared word does not.
        pats = [re.compile(r"\b%s\b" % re.escape(w.lower())) for w in words]

        def score(h):
            n = h["name"].lower()
            pts = 2 * bool(pats[0].search(n)) + sum(bool(p.search(n)) for p in pats[1:])
            return (pts, h["template"], -len(n))
        best = max(found.values(), key=score)
        return best if score(best)[0] >= 2 else None

    def missing(self):
        rows = self.api("/part/?low_stock=true&active=true&limit=500")
        rows = rows.get("results", rows) if isinstance(rows, dict) else rows
        return [self.brief(p) for p in rows]

    def part(self, pk):
        """Everything the book-in form needs about one article."""
        p = self.api("/part/%d/" % pk)
        out = self.brief(p)
        out["default_expiry"] = p.get("default_expiry") or 0
        out["variants"] = []
        if p.get("is_template"):
            rows = self.api("/part/?variant_of=%d&active=true&limit=200" % pk)
            rows = rows.get("results", rows) if isinstance(rows, dict) else rows
            out["variants"] = [self.brief(v) for v in rows]
        out["pack"] = None
        tpl = self.pack_template()
        if tpl and not p.get("is_template"):
            prm = self.api("/parameter/?model_type=part&model_id=%d&template=%d" % (pk, tpl))
            prm = prm.get("results", prm) if isinstance(prm, dict) else prm
            for row in prm or []:
                try:
                    v = float(str(row.get("data", "")).replace(",", "."))
                except ValueError:
                    continue
                if v > 0:
                    out["pack"] = v
                    out["pack_param"] = row["pk"]
        out["locations"] = self.locations()
        out["location"] = self.default_location() or p.get("default_location")
        return out

    def book(self, pk, packets, pack, location, expiry):
        p = self.part(pk)
        if p["template"]:
            raise ApiError("Pick a product, not the requirement.", 400)
        units = p["units"]
        if units:
            if not (pack and pack > 0):
                raise ApiError("Pack content is missing.", 400)
            amount = packets * pack
        else:
            amount = packets
        if not (amount > 0):
            raise ApiError("Nothing to book.", 400)
        rows = self.api("/stock/?part=%d&location=%d&limit=200" % (pk, location))
        rows = rows.get("results", rows) if isinstance(rows, dict) else rows
        same = next((r for r in rows if (r.get("expiry_date") or None) == (expiry or None)),
                    None)
        if same:
            self.api("/stock/add/", "POST",
                     {"items": [{"pk": same["pk"], "quantity": amount}],
                      "notes": "shopping-pantry"})
        else:
            body = {"part": pk, "quantity": amount, "location": location}
            if expiry:
                body["expiry_date"] = expiry
            self.api("/stock/", "POST", body)
        if units and pack != p.get("pack"):
            self.save_pack(pk, pack, p.get("pack_param"))
        return {"part": pk, "part_name": p["name"], "amount": amount, "units": units}

    def save_pack(self, pk, value, param_pk):
        """Remember the pack content on the product, as the pantry app does. A failure here
        is acceptable: the booked amount is right regardless."""
        try:
            if param_pk:
                self.api("/parameter/%d/" % param_pk, "PATCH", {"data": str(value)})
            elif self.pack_template():
                self.api("/parameter/", "POST", {"model_type": "part", "model_id": pk,
                                                 "template": self.pack_template(),
                                                 "data": str(value)})
        except ApiError as e:
            log("pack content not saved:", e)


# ---- The application -----------------------------------------------------------------------

class App:
    def __init__(self, cfg, open_store=True):
        self.cfg = cfg
        # --check runs without the database: run as root it would otherwise create the data
        # directory that systemd's DynamicUser wants to create (and own) itself.
        self.store = Store(cfg["database"]) if open_store else None
        self.hub = Hub()
        self.ai = AI(cfg["ai"]) if cfg.get("ai") else None
        self.speech = Speech(cfg["speech"]) if cfg.get("speech") else None
        self.pantry = Pantry(cfg["pantry"]) if cfg.get("pantry") else None

    def changed(self, list_id):
        self.hub.publish({"list": list_id})

    # -- lists

    def get_config(self, req):
        return {"version": VERSION, "language": self.cfg["language"],
                "sections": self.cfg["sections"],
                "features": {"ai": bool(self.ai), "speech": bool(self.speech),
                             "tts": bool(self.speech and self.speech.tts_url),
                             "pantry": bool(self.pantry)},
                "pantry_url": self.pantry.app_url if self.pantry else ""}

    def get_lists(self, req):
        return self.store.query(
            "SELECT l.id, l.name, l.created_at,"
            " (SELECT COUNT(*) FROM items i WHERE i.list_id = l.id AND i.done = 0) AS open"
            " FROM lists l WHERE l.archived = 0 ORDER BY l.id")

    def list_ids(self):
        return {r["id"] for r in self.get_lists(None)}

    def post_lists(self, req):
        name = str(req.body.get("name") or "").strip()[:100]
        lid, _ = self.store.execute("INSERT INTO lists (name, created_at) VALUES (?, ?)",
                                    (name, now_iso()))
        self.changed(lid)
        return {"id": lid}

    def patch_list(self, req, lid):
        if "name" in req.body:
            self.store.execute("UPDATE lists SET name = ? WHERE id = ?",
                               (str(req.body["name"]).strip()[:100], lid))
        if req.body.get("archived"):
            if len(self.list_ids()) <= 1:
                raise ApiError("The last list cannot be removed.")
            self.store.execute("UPDATE lists SET archived = 1 WHERE id = ?", (lid,))
        self.changed(lid)
        return {"ok": True}

    def get_list(self, req, lid):
        rows = self.store.query("SELECT * FROM lists WHERE id = ?", (lid,))
        if not rows:
            raise ApiError("No such list.", 404)
        items = self.store.query(
            "SELECT * FROM items WHERE list_id = ? AND cleared = 0 ORDER BY id", (lid,))
        out = rows[0]
        out["items"] = [item_out(r) for r in items]
        return out

    # -- items

    def insert_item(self, lid, it, who):
        iid, _ = self.store.execute(
            "INSERT INTO items (list_id, name, quantity, section, note, created_by, created_at,"
            " pantry_hint) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (lid, it["name"], it.get("quantity", ""), it.get("section", ""),
             it.get("note", ""), who, now_iso(), it.get("pantry_hint")))
        return iid

    def clean_item(self, it):
        name = str(it.get("name") or "").strip()[:200]
        hint = it.get("pantry_hint")
        return {"name": name, "quantity": str(it.get("quantity") or "").strip()[:50],
                "section": it.get("section") if it.get("section") in self.cfg["sections"] else "",
                "note": str(it.get("note") or "").strip()[:300],
                "pantry_hint": hint if isinstance(hint, int) else None}

    def post_items(self, req, lid):
        """One item or {"items": [...]} (typed without an AI, or a confirmed suggestion)."""
        batch = req.body.get("items") if "items" in req.body else [req.body]
        ids = []
        for it in batch:
            it = self.clean_item(it)
            if it["name"]:
                ids.append(self.insert_item(lid, it, req.device))
        if not ids:
            raise ApiError("Nothing to add.")
        self.changed(lid)
        return {"ids": ids}

    def item_row(self, iid):
        rows = self.store.query("SELECT * FROM items WHERE id = ?", (iid,))
        if not rows:
            raise ApiError("No such item.", 404)
        return rows[0]

    def update_item(self, iid, b, who):
        sets, args = [], []
        for key, limit in (("name", 200), ("quantity", 50), ("note", 300)):
            if key in b:
                sets.append(key + " = ?")
                args.append(str(b[key]).strip()[:limit])
        if "section" in b:
            sets.append("section = ?")
            args.append(b["section"] if b["section"] in self.cfg["sections"] else "")
        if "done" in b:
            sets += ["done = ?", "done_by = ?", "done_at = ?"]
            args += [1 if b["done"] else 0, who if b["done"] else "",
                     now_iso() if b["done"] else None]
        if "list_id" in b:
            sets.append("list_id = ?")
            args.append(int(b["list_id"]))
        if sets:
            self.store.execute("UPDATE items SET %s WHERE id = ?" % ", ".join(sets),
                               args + [iid])

    def reopen_item(self, row, who):
        """Untick an item. If the same thing is still open on that list (the rest left by a
        partial tick), the two become one again: 2 + 1 packs reopened are 3 packs, not two
        lines (reported 2026-10-23). Merged only with the same unit, or with no amounts at all;
        "2 Päckchen" and "500 g" stay apart. Returns the undo steps."""
        ops = [{"op": "restore_item", "row": row}]
        self.update_item(row["id"], {"done": False}, who)
        mine = parse_qty(row["quantity"])
        for other in self.store.query(
                "SELECT * FROM items WHERE list_id = ? AND done = 0 AND cleared = 0 AND id != ?"
                " AND lower(name) = lower(?)", (row["list_id"], row["id"], row["name"])):
            theirs = parse_qty(other["quantity"])
            if mine and theirs and mine[1].lower() == theirs[1].lower():
                quantity = fmt_qty(mine[0] + theirs[0], mine[1], self.cfg["language"])
            elif not row["quantity"] and not other["quantity"]:
                quantity = ""
            else:
                continue
            ops.append({"op": "restore_item", "row": other})
            self.store.execute("UPDATE items SET quantity = ? WHERE id = ?", (quantity, other["id"]))
            self.store.execute("DELETE FROM items WHERE id = ?", (row["id"],))
            break
        return ops

    def patch_item(self, req, iid):
        row = self.item_row(iid)
        if req.body.get("done") is False and row["done"]:
            self.reopen_item(row, req.device)
            self.changed(row["list_id"])
            return {"ok": True}
        self.update_item(iid, req.body, req.device)
        self.changed(row["list_id"])
        if "list_id" in req.body:
            self.changed(int(req.body["list_id"]))
        return {"ok": True}

    def delete_item(self, req, iid):
        """Deleted by hand (swipe or the form). Leaves an undo step like an AI command does, so
        a slip of the thumb costs one tap."""
        row = self.item_row(iid)
        self.store.execute("DELETE FROM items WHERE id = ?", (iid,))
        undo_id, _ = self.store.execute("INSERT INTO undo (created_at, ops) VALUES (?, ?)",
                                        (now_iso(), json.dumps([{"op": "restore_item", "row": row}])))
        self.changed(row["list_id"])
        return {"ok": True, "undo": undo_id}

    def split_item(self, row, got, rest, who):
        """Only part was bought: the item is ticked with what was got, and the rest stays on
        the list as a new open item. Returns the undo steps."""
        got, rest = str(got).strip()[:50], str(rest).strip()[:50]
        if not got or not rest:
            raise ApiError("Both the amount got and the rest are needed.")
        self.store.execute("UPDATE items SET quantity = ?, done = 1, done_by = ?, done_at = ?"
                           " WHERE id = ?", (got, who, now_iso(), row["id"]))
        new = self.insert_item(row["list_id"], {
            "name": row["name"], "quantity": rest, "section": row["section"],
            "note": row["note"], "pantry_hint": row.get("pantry_hint")}, who)
        return [{"op": "restore_item", "row": row}, {"op": "delete_item", "id": new}]

    def post_item_partial(self, req, iid):
        row = self.item_row(iid)
        ops = self.split_item(row, req.body.get("got", ""), req.body.get("rest", ""), req.device)
        undo_id, _ = self.store.execute("INSERT INTO undo (created_at, ops) VALUES (?, ?)",
                                        (now_iso(), json.dumps(ops)))
        self.changed(row["list_id"])
        return {"ok": True, "undo": undo_id}

    def post_clear_done(self, req, lid):
        # Hidden, not deleted: the purchase history is made of these rows.
        _, n = self.store.execute(
            "UPDATE items SET cleared = 1 WHERE list_id = ? AND done = 1 AND cleared = 0", (lid,))
        self.changed(lid)
        return {"removed": n}

    def get_history(self, req):
        """Bought items of the last 90 days, newest first, for the history view. The phone
        groups them by its own local day; the server does not know the household's time zone."""
        since = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat(timespec="seconds")
        sql = ("SELECT i.id, i.name, i.quantity, i.section, i.done_at, i.done_by,"
               " l.id AS list_id, l.name AS list_name FROM items i JOIN lists l ON l.id = i.list_id"
               " WHERE i.done = 1 AND i.done_at >= ? AND l.archived = 0")   # a removed list is gone
        args = [since]
        # ?list=N: the history of one list, as the app's History tab shows it.
        if req.query.get("list", [""])[0].isdigit():
            sql += " AND i.list_id = ?"
            args.append(int(req.query["list"][0]))
        return self.store.query(sql + " ORDER BY i.done_at DESC LIMIT 1000", args)

    # -- speech

    def post_transcribe(self, req):
        if not self.speech:
            raise ApiError("No speech service is configured.", 404)
        if not req.raw:
            raise ApiError("No recording.")
        ctype = req.headers.get("Content-Type", "audio/wav")
        text = self.speech.transcribe(req.raw, ctype)
        log("dictation from %s: %r" % (req.device or "?", text[:300]))
        return {"text": text}

    def post_speak(self, req):
        if not (self.speech and self.speech.tts_url):
            raise ApiError("No voice output is configured.", 404)
        text = str(req.body.get("text") or "").strip()[:1000]
        if not text:
            raise ApiError("Nothing to say.")
        return Binary(self.speech.speak(text), "audio/wav")

    # -- AI commands

    def history(self):
        """What this household buys, from ticked items of the last 120 days: name, how often,
        last time. The AI uses it for "the usual" and "what did I forget?"."""
        since = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat(timespec="seconds")
        return self.store.query(
            "SELECT name, COUNT(*) AS times, MAX(done_at) AS last FROM items"
            " WHERE done = 1 AND done_at >= ? GROUP BY lower(name)"
            " HAVING COUNT(*) >= 2 ORDER BY times DESC, last DESC LIMIT 40", (since,))

    def state_text(self, current):
        """The app as the model sees it: compact lines, numbers it may refer to."""
        lines = ["Today: %s" % date.today().isoformat()]
        lists = self.get_lists(None)
        for l in lists:
            name = l["name"] or "(main list)"
            lines.append("")
            lines.append("List %d: %s%s" % (l["id"], name,
                                            "   <- current" if l["id"] == current else ""))
            items = self.store.query(
                "SELECT * FROM items WHERE list_id = ? AND cleared = 0 ORDER BY done, id",
                (l["id"],))
            if not items:
                lines.append("  (empty)")
            for it in items:
                bits = ["  #%d %s" % (it["id"], it["name"])]
                if it["quantity"]:
                    bits.append("qty " + it["quantity"])
                if it["section"]:
                    bits.append(it["section"])
                if it["note"]:
                    bits.append("note: " + it["note"])
                if it["done"]:
                    p = json.loads(it["pantry"]) if it.get("pantry") else None
                    state = "bought"
                    if self.pantry:
                        state += ", pantry: " + (p["state"] if p else "not decided")
                    bits.append(state)
                else:
                    bits.append("open")
                lines.append(" | ".join(bits))
        hist = self.history()
        if hist:
            lines.append("")
            lines.append("Purchase history (name, times bought in 120 days, last bought):")
            for h in hist:
                lines.append("  %s | %dx | %s" % (h["name"], h["times"], (h["last"] or "")[:10]))
        lines.append("")
        lines.append("Pantry: " + ("connected" if self.pantry else "not connected"))
        return "\n".join(lines)

    def lookup(self, terms):
        out = []
        for term in [str(t)[:40] for t in terms][:8]:
            try:
                hits = self.pantry.search(term, 5)
            except ApiError as e:
                out.append("%s: lookup failed (%s)" % (term, e))
                continue
            if not hits:
                out.append("%s: nothing in the pantry" % term)
            for h in hits:
                # A requirement's stock is the SUM of its products; without saying so the model
                # read "Reis 22.5 kg" next to its two products as a third kind (2026-10-23).
                out.append("%s: %s%s, in stock %g %s%s" % (
                    term, h["name"],
                    " (requirement: total of all its products below)" if h["template"] else "",
                    h["in_stock"], h["units"] or "pieces",
                    ", target %g" % h["minimum"] if h["minimum"] else ""))
        return "\n".join(out)

    def post_command(self, req):
        """A sentence -> actions, executed at once; the reply carries an undo id."""
        if not self.ai:
            raise ApiError("No AI is configured.", 404)
        text = str(req.body.get("text") or "").strip()[:2000]
        if not text:
            raise ApiError("Nothing to do.")
        try:
            current = int(req.body.get("list"))
        except (TypeError, ValueError):
            current = min(self.list_ids())
        system = COMMAND_PROMPT.format(
            sections=" | ".join(self.cfg["sections"]), language=self.cfg["language"],
            pantry_actions=PANTRY_ACTIONS if self.pantry else "")
        parts = ["STATE\n" + self.state_text(current)]
        # The last exchange or two, so "and eggs too" or the answer to a question makes sense.
        for turn in (req.body.get("context") or [])[-4:]:
            if isinstance(turn, dict) and turn.get("text"):
                who = "Assistant" if turn.get("role") == "assistant" else "Person"
                parts.append("EARLIER %s: %s" % (who, str(turn["text"])[:500]))
        parts.append("COMMAND\n" + text)
        started = time.time()
        answer = {}
        for _ in range(3):
            answer = parse_json(self.ai.chat(system, "\n\n".join(parts)))
            if answer.get("lookups") and self.pantry and not answer.get("actions"):
                parts.append("LOOKUP RESULTS\n" + self.lookup(answer["lookups"]))
                continue
            break
        result = self.execute(answer.get("actions") or [], current, req.device)
        reply = str(answer.get("reply") or "").strip()
        # What was asked and what the model did: without it a bad result cannot be traced
        # (2026-10-23: a new list "Eiermilch und Kaffee" appeared, and nothing said why).
        log("command from %s on list %s: %r -> %s | reply %r" % (
            req.device or "?", current, text[:300],
            json.dumps(answer.get("actions") or [], ensure_ascii=False)[:1500], reply[:300]))
        if result["errors"]:
            reply = (reply + " " if reply else "") + " ".join(result["errors"])
        log("command: %d actions, %d errors in %.1f s" % (
            len(result["done"]), len(result["errors"]), time.time() - started))
        return {"reply": reply, "question": bool(answer.get("question")),
                "undo": result["undo"], "show_list": result["show_list"],
                "in_pantry": result["in_pantry"], "count": len(result["done"]),
                "booked": "book" in result["done"]}

    def execute(self, actions, current, who):
        """Validate and run the model's actions. Each successful one leaves its inverse in
        `ops`; a failing one is reported and skipped, the others still run."""
        sections = self.cfg["sections"]
        lists = self.list_ids()
        refs = {}
        ops, done, errors, touched = [], [], [], set()
        show = None
        hint_jobs = []
        in_pantry = []            # added items the pantry already holds: "buy it anyway?"

        def list_of(v):
            if v in (None, ""):
                return current
            if isinstance(v, str) and v in refs:
                return refs[v]
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise ApiError("unknown list %r" % v)
            if v not in lists:
                raise ApiError("unknown list %r" % v)
            return v

        def item_of(v):
            try:
                row = self.item_row(int(v))
            except (TypeError, ValueError, ApiError):
                raise ApiError("unknown item %r" % v)
            if row["cleared"]:
                raise ApiError("unknown item %r" % v)
            return row

        for a in actions if isinstance(actions, list) else []:
            if not isinstance(a, dict):
                continue
            kind = a.get("type")
            try:
                if kind == "add":
                    lid = list_of(a.get("list"))
                    it = self.clean_item(a)
                    if not it["name"]:
                        continue
                    if it["section"] not in sections:
                        it["section"] = sections[-1]
                    iid = self.insert_item(lid, it, who)
                    ops.append({"op": "delete_item", "id": iid})
                    terms = [str(x)[:40] for x in a.get("search") or [] if str(x).strip()][:3]
                    if self.pantry and terms:
                        hint_jobs.append((iid, it["name"], terms))
                    touched.add(lid)
                elif kind == "partial":
                    row = item_of(a.get("item"))
                    ops.extend(self.split_item(row, a.get("got", ""), a.get("rest", ""), who))
                    touched.add(row["list_id"])
                elif kind in ("check", "uncheck", "update", "move", "remove"):
                    row = item_of(a.get("item"))
                    ops.append({"op": "restore_item", "row": row})
                    if kind == "remove":
                        self.store.execute("DELETE FROM items WHERE id = ?", (row["id"],))
                    elif kind == "move":
                        lid = list_of(a.get("list"))
                        self.update_item(row["id"], {"list_id": lid}, who)
                        touched.add(lid)
                    elif kind == "update":
                        self.update_item(row["id"], {k: a[k] for k in
                                         ("name", "quantity", "section", "note") if k in a}, who)
                    elif kind == "uncheck":
                        ops.pop()                       # reopen_item records its own steps
                        ops.extend(self.reopen_item(row, who))
                    else:
                        self.update_item(row["id"], {"done": True}, who)
                    touched.add(row["list_id"])
                elif kind == "create_list":
                    name = str(a.get("name") or "").strip()[:100]
                    lid, _ = self.store.execute(
                        "INSERT INTO lists (name, created_at) VALUES (?, ?)", (name, now_iso()))
                    lists.add(lid)
                    if a.get("ref"):
                        refs[str(a["ref"])] = lid
                    ops.append({"op": "archive_list", "id": lid})
                    show = show or lid
                    touched.add(lid)
                elif kind == "rename_list":
                    lid = list_of(a.get("list"))
                    old = self.store.query("SELECT name FROM lists WHERE id = ?", (lid,))[0]
                    self.store.execute("UPDATE lists SET name = ? WHERE id = ?",
                                       (str(a.get("name") or "").strip()[:100], lid))
                    ops.append({"op": "rename_list", "id": lid, "name": old["name"]})
                    touched.add(lid)
                elif kind == "clear_done":
                    lid = list_of(a.get("list"))
                    rows = self.store.query("SELECT * FROM items WHERE list_id = ? AND done = 1"
                                            " AND cleared = 0", (lid,))
                    for row in rows:
                        ops.append({"op": "restore_item", "row": row})
                    self.store.execute("UPDATE items SET cleared = 1 WHERE list_id = ?"
                                       " AND done = 1", (lid,))
                    touched.add(lid)
                elif kind == "show_list":
                    show = list_of(a.get("list"))
                elif kind in ("book", "pantry_skip") and self.pantry:
                    row = item_of(a.get("item"))
                    if kind == "pantry_skip":
                        ops.append({"op": "restore_item", "row": row})
                        state = {"state": "skipped"}
                    else:
                        # Not undoable: stock may be used the next minute. Said in the reply.
                        state = self.book_by_voice(row, a.get("packets"))
                    if not row["done"]:
                        self.update_item(row["id"], {"done": True}, who)
                    self.store.execute("UPDATE items SET pantry = ? WHERE id = ?",
                                       (json.dumps(state), row["id"]))
                    touched.add(row["list_id"])
                else:
                    continue
                done.append(kind)
            except ApiError as e:
                errors.append(str(e))

        # Pantry hints for new items, all at once (a search is a network round trip).
        if hint_jobs:
            with ThreadPoolExecutor(8) as pool:
                hints = list(pool.map(lambda j: self.pantry.hint(j[1], j[2]), hint_jobs))
            for (iid, _, _), h in zip(hint_jobs, hints):
                if h:
                    self.store.execute("UPDATE items SET pantry_hint = ? WHERE id = ?",
                                       (h["pk"], iid))
                    if h["in_stock"] > 0:
                        in_pantry.append({"name": h["name"], "in_stock": h["in_stock"],
                                          "units": h["units"]})

        undo_id = None
        if ops:
            undo_id, _ = self.store.execute("INSERT INTO undo (created_at, ops) VALUES (?, ?)",
                                            (now_iso(), json.dumps(ops)))
            # Undo is for "oops, not that", not for last week: older steps are dropped.
            old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(timespec="seconds")
            self.store.execute("DELETE FROM undo WHERE created_at < ?", (old,))
        for lid in touched:
            self.changed(lid)
        return {"done": done, "errors": errors, "undo": undo_id, "show_list": show,
                "in_pantry": in_pantry}

    def book_by_voice(self, row, packets):
        """"6 almond milk into the pantry": the form's booking with its defaults. The article
        is the one the AI pointed at when the item was added, else the best hint now; a
        requirement books onto its product with the most stock. A measured article needs a
        known pack content: without one the form has to be used, a guess would be stock."""
        pantry = self.pantry
        try:
            packets = float(packets)
        except (TypeError, ValueError):
            packets = 0
        if not packets > 0:
            raise ApiError("How many go into the pantry?")
        pk = row.get("pantry_hint")
        if not pk:
            h = pantry.hint(row["name"], [])
            pk = h and h["pk"]
        if not pk:
            raise ApiError("No pantry article found for %s." % row["name"])
        part = pantry.part(pk)
        if part["template"]:
            if not part["variants"]:
                raise ApiError("%s has no product to book onto." % part["name"])
            part = pantry.part(max(part["variants"], key=lambda v: v["in_stock"])["pk"])
        if part["units"] and not part.get("pack"):
            raise ApiError("Pack content of %s is unknown; please use the form." % part["name"])
        location = part.get("location")
        if not location:
            raise ApiError("No pantry location to book into.")
        expiry = None
        if part.get("default_expiry"):
            expiry = (date.today() + timedelta(days=int(part["default_expiry"]))).isoformat()
        state = pantry.book(part["pk"], packets, part.get("pack"), location, expiry)
        state.update({"state": "booked", "packets": packets})
        return state

    def post_undo(self, req, uid):
        rows = self.store.query("SELECT * FROM undo WHERE id = ?", (uid,))
        if not rows:
            raise ApiError("Nothing to undo.", 404)
        touched = set()
        for op in reversed(json.loads(rows[0]["ops"])):
            if op["op"] == "delete_item":
                for r in self.store.query("SELECT list_id FROM items WHERE id = ?", (op["id"],)):
                    touched.add(r["list_id"])
                self.store.execute("DELETE FROM items WHERE id = ?", (op["id"],))
            elif op["op"] == "restore_item":
                row = op["row"]
                cols = ", ".join(row)
                marks = ", ".join("?" * len(row))
                self.store.execute("INSERT OR REPLACE INTO items (%s) VALUES (%s)"
                                   % (cols, marks), list(row.values()))
                touched.add(row["list_id"])
            elif op["op"] == "archive_list":
                self.store.execute("UPDATE lists SET archived = 1 WHERE id = ?", (op["id"],))
                touched.add(op["id"])
            elif op["op"] == "rename_list":
                self.store.execute("UPDATE lists SET name = ? WHERE id = ?",
                                   (op["name"], op["id"]))
                touched.add(op["id"])
        self.store.execute("DELETE FROM undo WHERE id = ?", (uid,))
        for lid in touched:
            self.changed(lid)
        return {"ok": True}

    # -- pantry bridge

    def need_pantry(self):
        if not self.pantry:
            raise ApiError("No pantry is connected.", 404)
        return self.pantry

    def get_pantry_search(self, req):
        return self.need_pantry().search(req.query.get("q", [""])[0], 8)

    def get_pantry_missing(self, req):
        return self.need_pantry().missing()

    def get_pantry_part(self, req, pk):
        return self.need_pantry().part(pk)

    def post_item_pantry(self, req, iid):
        """Book (part of) a bought item into the pantry, or mark it as not for the pantry."""
        pantry = self.need_pantry()
        row = self.item_row(iid)
        b = req.body
        if b.get("skip"):
            state = {"state": "skipped"}
        else:
            try:
                pk = int(b["part"])
                packets = float(b.get("packets") or 0)
                pack = float(b["pack"]) if b.get("pack") not in (None, "") else None
                location = int(b["location"])
            except (KeyError, TypeError, ValueError):
                raise ApiError("Article, amount and location are needed.")
            expiry = b.get("expiry") or None
            if expiry:
                try:
                    date.fromisoformat(expiry)
                except ValueError:
                    raise ApiError("Best-before date is not a date.")
            state = pantry.book(pk, packets, pack, location, expiry)
            state.update({"state": "booked", "packets": packets})
        self.store.execute("UPDATE items SET pantry = ? WHERE id = ?",
                           (json.dumps(state), iid))
        self.changed(row["list_id"])
        return state

    def delete_item_pantry(self, req, iid):
        """Undo a 'skipped' decision. A booking is not undone here: stock may already have
        moved, so that is corrected in the pantry app itself."""
        row = self.item_row(iid)
        p = json.loads(row["pantry"]) if row.get("pantry") else None
        if p and p.get("state") == "booked":
            raise ApiError("Booked stock is corrected in the pantry app.", 409)
        self.store.execute("UPDATE items SET pantry = NULL WHERE id = ?", (iid,))
        self.changed(row["list_id"])
        return {"ok": True}


# ---- HTTP ----------------------------------------------------------------------------------

ROUTES = [
    ("GET", r"/api/config", "get_config"),
    ("GET", r"/api/lists", "get_lists"),
    ("GET", r"/api/history", "get_history"),
    ("POST", r"/api/lists", "post_lists"),
    ("GET", r"/api/lists/(\d+)", "get_list"),
    ("PATCH", r"/api/lists/(\d+)", "patch_list"),
    ("POST", r"/api/lists/(\d+)/items", "post_items"),
    ("POST", r"/api/lists/(\d+)/clear-done", "post_clear_done"),
    ("POST", r"/api/command", "post_command"),
    ("POST", r"/api/undo/(\d+)", "post_undo"),
    ("POST", r"/api/speak", "post_speak"),
    ("PATCH", r"/api/items/(\d+)", "patch_item"),
    ("DELETE", r"/api/items/(\d+)", "delete_item"),
    ("POST", r"/api/items/(\d+)/pantry", "post_item_pantry"),
    ("POST", r"/api/items/(\d+)/partial", "post_item_partial"),
    ("DELETE", r"/api/items/(\d+)/pantry", "delete_item_pantry"),
    ("POST", r"/api/transcribe", "post_transcribe"),
    ("GET", r"/api/pantry/search", "get_pantry_search"),
    ("GET", r"/api/pantry/missing", "get_pantry_missing"),
    ("GET", r"/api/pantry/part/(\d+)", "get_pantry_part"),
]
ROUTES = [(m, re.compile("^" + p + "/?$"), h) for m, p, h in ROUTES]


class Request:
    def __init__(self, handler, body, raw, query):
        self.headers = handler.headers
        self.body = body
        self.raw = raw
        self.query = query
        # Who did it: the name the phone gave itself. Nobody logs in (the network is the
        # boundary); this only says who ticked what.
        self.device = urllib.parse.unquote(handler.headers.get("X-Device", ""))[:40]


class Handler(BaseHTTPRequestHandler):
    server_version = "shopping-pantry/" + VERSION
    protocol_version = "HTTP/1.1"
    app = None                       # set in main()

    def log_message(self, fmt, *args):
        pass                         # quiet; errors are logged where they happen

    def send(self, status, payload, ctype="application/json; charset=utf-8"):
        data = payload if isinstance(payload, bytes) else \
            json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def dispatch(self, method):
        url = urllib.parse.urlsplit(self.path)
        path = url.path
        if path == "/api/events" and method == "GET":
            return self.events()
        if not path.startswith("/api/"):
            if method != "GET":
                return self.send(405, {"error": "method not allowed"})
            return self.static(path)
        for m, rx, name in ROUTES:
            match = rx.match(path)
            if m == method and match:
                break
        else:
            return self.send(404, {"error": "not found"})
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return self.send(413, {"error": "too large"})
        raw = self.rfile.read(length) if length else b""
        body = {}
        if raw and "json" in (self.headers.get("Content-Type") or ""):
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                return self.send(400, {"error": "bad JSON"})
            if not isinstance(body, dict):
                return self.send(400, {"error": "expected an object"})
        req = Request(self, body, raw, urllib.parse.parse_qs(url.query))
        try:
            args = [int(g) for g in match.groups()]
            result = getattr(self.app, name)(req, *args)
            if isinstance(result, Binary):
                self.send(200, result.data, result.ctype)
            else:
                self.send(200, result)
        except ApiError as e:
            log("%s %s -> %s" % (method, path, e))
            self.send(e.status, {"error": str(e)})
        except Exception as e:  # noqa: BLE001 -- a bug must not take the server down
            log("%s %s -> BUG %r" % (method, path, e))
            self.send(500, {"error": "internal error: %s" % e})

    def static(self, path):
        rel = path.lstrip("/") or "index.html"
        full = os.path.normpath(os.path.join(APP_DIR, rel))
        if not full.startswith(APP_DIR + os.sep) or not os.path.isfile(full):
            full = os.path.join(APP_DIR, "index.html")   # single page: unknown paths -> app
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if full.endswith(".webmanifest"):
            ctype = "application/manifest+json"
        if ctype.startswith("text/") or ctype.endswith(("json", "javascript", "svg+xml")):
            ctype += "; charset=utf-8"
        with open(full, "rb") as f:
            self.send(200, f.read(), ctype)

    def events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        hub = self.app.hub
        seq = hub.seq
        try:
            self.wfile.write(b"retry: 3000\n\n")
            self.wfile.flush()
            while True:
                new, payloads = hub.wait(seq, SSE_HEARTBEAT)
                if new == seq:
                    self.wfile.write(b": ping\n\n")
                else:
                    seq = new
                    lists = sorted({p["list"] for p in payloads}) or [None]
                    for lid in lists:
                        self.wfile.write(("data: %s\n\n" % json.dumps({"list": lid})).encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PATCH(self):
        self.dispatch("PATCH")

    def do_DELETE(self):
        self.dispatch("DELETE")


def main():
    ap = argparse.ArgumentParser(description="shopping-pantry server")
    ap.add_argument("--config", default=os.environ.get("SHOPPING_CONFIG", ""),
                    help="path to shopping.json (default: $SHOPPING_CONFIG, else built-ins)")
    ap.add_argument("--check", action="store_true",
                    help="load the config, test every configured service, and exit")
    args = ap.parse_args()
    cfg = load_config(args.config)
    app = App(cfg, open_store=not args.check)
    if args.check:
        return check(app)
    Handler.app = app
    host, _, port = cfg["listen"].rpartition(":")
    srv = ThreadingHTTPServer((host or "0.0.0.0", int(port)), Handler)
    srv.daemon_threads = True
    log("shopping-pantry %s on %s (ai: %s, speech: %s, pantry: %s)"
        % (VERSION, cfg["listen"], bool(app.ai), bool(app.speech), bool(app.pantry)))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


def check(app):
    """Exit 0 when every configured service answers. For installers and humans."""
    ok = True
    print("database:", app.cfg["database"], "(not opened by --check)")
    if app.ai:
        try:
            t = time.time()
            system = COMMAND_PROMPT.format(
                sections=" | ".join(app.cfg["sections"]), language=app.cfg["language"],
                pantry_actions="")
            answer = parse_json(app.ai.chat(system, "STATE\nList 1: (main list)   <- current\n"
                                                    "  (empty)\n\nCOMMAND\nmilk and bread"))
            print("ai: ok, %d actions in %.1f s" % (len(answer.get("actions") or []),
                                                    time.time() - t))
        except ApiError as e:
            ok = False
            print("ai: FAILED:", e)
    else:
        print("ai: not configured")
    print("speech:", "configured (" + app.speech.url + ")" if app.speech else "not configured")
    if app.speech and app.speech.tts_url:
        try:
            print("voice output: ok, %d bytes" % len(app.speech.speak("Test")))
        except ApiError as e:
            ok = False
            print("voice output: FAILED:", e)
    if app.pantry:
        try:
            locs = app.pantry.locations()
            print("pantry: ok, %d locations, book into %s" % (
                len(locs), app.pantry.default_location() or "(no match for book_location!)"))
        except ApiError as e:
            ok = False
            print("pantry: FAILED:", e)
    else:
        print("pantry: not configured")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
