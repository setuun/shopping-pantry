# App guide: look and feel of the household apps

The household's small web apps (inventree-pantry, shopping-pantry, and whatever comes next)
look and behave alike, without a framework. This is the checklist. Every rule here was asked
for or found wrong at least once; read it before building a screen.

## Stack

- One HTML file for the front end, inline CSS and JS, no build, no framework, no CDN.
  Server, if any: Python standard library only. Everything vendored in the repo.
- Phone first (390 px wide is the test size), works on a desktop too.
- Every word on screen through `t("area.key")`, texts in `app/i18n/*.json`, a check script.

## Look

- **Always dark**, whatever the phone is set to, as inventree-pantry: Material's dark theme,
  base `#121212`, cards `#1E1E1E`, text white at 87 % / 60 %, lines at 12 %; a light accent tint
  carries dark text. Colours as CSS variables on `:root`. **`color-scheme: dark` is mandatory**,
  or select lists, date pickers and checkboxes are drawn light.
- `<meta name="color-scheme" content="dark">` in the head and a dark `html` background, or the
  canvas flashes white while the keyboard slides.
- Buttons: `-webkit-tap-highlight-color: transparent`, `user-select: none`,
  `-webkit-touch-callout: none`, or Android lays a blue box over them (worst on a long press);
  give the main action its own pressed look instead.
- `focus()` (which pops the keyboard) only where the person just asked to type a name ("New
  list", "Rename"); never when a bar or panel merely opens.
- See-through colours (the 12 % lines) show what is under them: put lines on opaque surfaces.
- Never use "text colour as background" for a surface (it turns white in dark mode). Toasts,
  sheets and menus have their own variables.
- Each app has its accent (shopping-pantry green `#7cc49d`, inventree-pantry blue `#90caf9`),
  cards with 12 px radius and a 1 px line. One card per group, rows separated by lines, not a
  tile per item.
- Small grey uppercase section headings. Header: the current thing in large type, a muted
  summary line below ("5 open · 3 done").
- Amounts, counts and states are **labels (pills) at the right edge** of a row, not text glued
  to the name.
- Installed as an app: `theme-color` per colour scheme (= the page background) and a manifest
  `background_color`/`theme_color` that is dark; a light value is a white strip under the app.
- A loading curtain (the app's mark over a spinner, markup not script) until the first screen is
  drawn with its data; if the server is missing, say so on the curtain.
- Touch targets at least 44 px. Screenshot every screen, with the phone set to light AND to dark, before calling it done.

## Frame

```
 [mark] App name (version)                ⚙     app bar: app-wide; ⚙ = settings page
 Current thing ▾                          ⋯     context bar: ⋯ = this thing only
 5 open · 3 done
   … content …
 Tab  Tab   ( main action )   Tab  Tab          bottom bar, the main action raised round in the middle
```

- **Tabs, not overlays.** Bottom-bar buttons and ⚙ switch the view between the bars; the bars
  never disappear. Tabs about the current thing show it in the context bar; others (a chooser,
  settings) put their own title there. Back goes from any tab to the start tab first.
- App bar as in inventree-pantry: mark in the accent colour, name, version as a quiet chip.
- ⋯ never mixes scopes: it acts on the thing shown, nothing app-wide.
- Bottom bar: icon over an 11 px bold word, muted; the active one in the accent colour. The main
  action (microphone) is a 64 px circle rising 24 px out of the bar, ringed in the bar's colour.
- Icons: filled Material icons on a 24 grid in `currentColor`, the `ICON` table of inventree-pantry.
  No emoji, no text glyphs as icons.

## Patterns

| Need | Pattern |
|---|---|
| ⋯ (more actions) | small menu dropping down from the button, headed with what it acts on; closes on a tap outside |
| Choosing among things (lists) | sheet from the bottom with a handle; the handle **must** drag the sheet down to close |
| Yes / no, a name to type | the same bottom sheet. **Never** `alert`, `confirm`, `prompt` |
| Long content (history, settings) | a tab between the bars, not a page over everything |
| Feedback after an action | toast at the bottom, above the input bar; with **Undo** when something changed |
| Delete an item | swipe left over a red "Delete" strip, plus "Delete" in the item's form; both with undo |
| Edit an item | tap the row: a form opens inline below it |
| Done / bought | round check at the left; done items grey and struck through, in their own group |
| Input | the raised centre button in the bottom bar, two modes switched by a long press and remembered: speak (tap = start, tap = stop) and type (tap = open the text line above the bar, tap = close); the line has an always-visible send button, greyed while empty |
| Microphone | as in the term app: round, 56 px, white symbol, slightly raised and see-through; red and pulsing while recording; stops after 10 s of silence |

## Behaviour

- **Back never leaves at once.** One spare history entry; back closes what is on top (menu,
  sheet, page, form), and at the top level "press back again to leave", the second press
  within 2.5 s leaves. On-screen back arrows call `history.back()`.
- Live state from other devices must not wipe a half-filled form (keep the open form across
  redraws).
- Every button that waits for the server: disabled at once, spinner after 150 ms.
- Things the AI or a swipe changes can be undone; whatever cannot (stock booked into InvenTree)
  says so.
- Concepts that are not obvious get one sentence where they are used (what a list is, what a
  store section is for), not a help page.
