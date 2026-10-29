# Changelog

## 0.7.2 (2026-10-29)

- **Empty a list** (⋯ → "Empty list", or by voice: "Liste leeren"): for a list made and then
  not shopped. Open items are deleted and stay out of the history, bought ones are cleared as
  before; asks first, and the toast has an undo. New endpoint `POST /api/lists/N/clear`, new
  AI action `clear_list`.

## 0.7.1 (2026-10-23) — first public release

- No grey scrollbar strip at the right of the typing line (it only scrolls once taller than its
  maximum); the paper plane sits optically centred in the send button.
- The reply line's text is vertically centred with its close button, now a Material icon.

## 0.7.0 (internal)

- **The centre button has two modes**, switched by a long press and remembered per device:
  *speak* (tap = start recording, tap = stop) and *type* (tap = open the typing line, tap =
  close it). The symbol shows which. The line's × is gone; its send button is always there and
  greyed while empty. Closing keeps what was typed.

## 0.6.3 (internal)

- No blue box over the microphone on a long press (Android's tap highlight and text selection
  are off for buttons); the microphone shrinks a little while pressed instead.

## 0.6.2 (internal)

- No white flash while the keyboard slides: `<meta name="color-scheme" content="dark">` and a
  dark canvas before any style. An app installed before 0.4.3 keeps its old light window colour
  until Chrome refreshes it (at most daily) or it is installed again.
- Opening the typing line no longer pops up the keyboard; tap into the field to type.

## 0.6.1 (internal)

- **Always dark**, as inventree-pantry (Material dark surfaces and text opacities), whatever the
  phone is set to; the green accent stays.
- The typing line can be closed again: the × beside it, a second long press on the microphone,
  a tap on the list (while it is empty), or back.

## 0.6.0 (internal)

Tabs, as in inventree-pantry: the app bar and the bottom bar stay, the view between them changes.

- **List · History · (microphone) · Pantry · Lists.** List, History and Pantry are about the
  current list, and the list bar says which. History shows what was bought *from this list*.
  Pantry shows what the pantry is missing, to tick and put on this list. Lists is where a list
  is chosen (it becomes the current one), created, renamed or removed.
- Settings (⚙) is a view between the bars too, no longer a page over everything.
- **Long-press the microphone to type**; a tap still speaks. A one-time hint says so.
- Back leads from any tab to List first; only there "press back again to leave".

## 0.5.0 (internal)

New frame, in the style of inventree-pantry.

- **App bar:** the app's mark, its name and the version; ⚙ opens the settings page (this
  device: name, read aloud; the version).
- **List bar** below it: which list this is, open/done; ⋯ is about this list only (clear done
  items, rename, remove).
- **Bottom bar:** Lists · History · the microphone · Pantry · Type. The microphone is the round
  centre, raised out of the bar; without a speech service the centre is "+". The typing line
  appears above the bar when asked for and goes away after sending (or with back).
- Icons are the filled Material set of inventree-pantry.

## 0.4.3 (internal)

- **No white strip under the installed app in dark mode:** the manifest's colours were light;
  the system bars now follow the page (`theme-color` per colour scheme, dark manifest).
- **Loading curtain** as in inventree-pantry: the app's mark over a spinner until the list is
  drawn with its data; a missing server is said there instead of a blank page.

## 0.4.2 (internal)

Menus, and the back button.

- ⋯ drops down a small menu from the button. "Your lists" stays a sheet from the bottom, and
  its handle now does what it promises: drag it down to close. The history is a page of its own
  with a back arrow.
- **Back no longer leaves the app at once** (as in inventree-pantry): it closes what is open
  (menu, sheet, page, form, suggestion); with nothing open, "press back again to leave", and
  only the second press within 2.5 s leaves.
- The history leaves out lists that were removed.

## 0.4.1 (internal)

- Reopening an item whose rest is still open joins them again: 2 + 1 packs reopened are 3 packs,
  not two lines. By hand and by voice, with undo.
- The AI only touches the items a command is about, and items on other lists only when that list
  is named ("reopen the carrots" also reopened yoghurt on another list).
- Dark mode: the toast was nearly white; native controls (select lists, date pickers,
  checkboxes) were white too (`color-scheme` was missing). Removing a list asks in the app's own
  sheet instead of the browser's dialog. The device-name dialog can be cancelled.

## 0.4.0 (internal)

- **The amount is a label at the right edge** of the row, so the name stays readable.
- **Got only part of it?** Tap the amount: "How much did you get?" with − / +. Ticking off
  marks what was got as bought, and the rest stays on the list as its own item. With undo.
  By voice too: "I only got two of the yoghurts".

## 0.3.2 (internal)

- **Swipe left to delete** an item, over a red "Delete" strip; a short swipe snaps back. The
  toast offers **undo**, and so does deleting from the item's form.

## 0.3.1 (internal)

Fixes from the first real use by voice.

- A new list is only created when someone asks for one; "the shopping list" means the current
  list. ("Eier, Milch und Kaffee", dictated, became a new list named after it.)
- Questions only answer, they never change the list.
- Another spelling of the same thing is the same item (Kaffee / Café).
- Words glued together by speech recognition are split ("Eiermilch" -> Eier, Milch).
- Every command and dictation is logged with its text and the actions taken.
- Item numbers are never reused, so undo cannot overwrite a newer item. Existing databases
  are converted once on start.

## 0.3.0 (internal)

How a list lives, made visible.

- **"All done":** once everything is ticked, a card at the top offers "Done – clear ticked
  items". A list is meant to stay; this is the step after shopping.
- **History** (⋯ → History): what was bought in the last 90 days, by day, across lists; "+"
  puts a thing back on the current list.
- "Your lists" explains the idea: one list that stays, more lists for other shops or occasions.

## 0.2.1 (internal)

Layout.

- The header names the current list in large type with "3 open · 4 done"; tapping it opens
  "Your lists" (switch, see what is open, create one). The menu (⋯) moved to the header: rename
  or remove the list, add what the pantry is missing, device name, read aloud.
- One card per store section instead of a tile per item; the device name only shows when
  someone else added or ticked an item; done items can be hidden.
- The command line is as high as the microphone (56 px), the send arrow sits inside it.
- "Section" is now "Store section", with a line saying the list is sorted by it.
- List names are typed into the sheet instead of the browser's prompt.

## 0.2.0 (internal)

The AI now runs the whole app, not only the list.

- **Commands instead of a list generator.** Typed or spoken, a sentence becomes actions and
  runs at once: add, tick off, untick, change amount or note, remove, move to another list,
  create or rename a list, clear done items, switch the screen to a list. "Milk and eggs",
  "got the bread", "the screws go on the DIY list", "make it 3 litres".
- **Undo** for every command, in the reply line.
- **Questions back** when a command is ambiguous; the answer is the next command.
- **Pantry by voice:** "do we still have rice?" is answered from InvenTree; "6 almond milk into
  the pantry" books with the form's defaults (pack content must be known). A booking is the
  one thing undo does not take back.
- **Recipes:** "ingredients for lasagne for 4" adds them, leaving out what the pantry holds.
- **The usual / what did I forget?** from the purchase history. "Remove done items" now hides
  them instead of deleting, which is what the history is made of.
- **Read replies aloud** (per device, optional `speech.tts_url`).
- New layout: one command line at the bottom, the round microphone next to it; after 10 s of
  silence the recording ends by itself.

## 0.1.0 (internal)

First version.

- Shared shopping lists, several at once (weekly shop, DIY store, …), live on every phone via
  Server-Sent Events. No login: each device names itself once, so you see who added or ticked
  what.
- Optional AI: a few words, typed or spoken, become a sorted list with amounts; vague wishes
  ("something for dinner") become concrete items. Shown as a suggestion to tick, not added
  blindly. OpenAI-compatible or Anthropic wire format.
- Optional dictation through any OpenAI-compatible transcription endpoint (whisper.cpp,
  faster-whisper, OpenAI). The browser records 16 kHz WAV; the server passes it on.
- Optional bridge to [inventree-pantry](https://github.com/setuun/inventree-pantry): the
  suggestion says how much is already in the pantry, "Missing from the pantry" fills the list
  from InvenTree's low-stock list, and a bought item can be booked into the pantry in part
  ("10 bought, 6 into the pantry, 4 stay in the kitchen"), following the pantry app's rules
  for pack content and best-before dates.
