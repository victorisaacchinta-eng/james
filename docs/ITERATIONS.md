# How JAMES was built: iterations

JAMES was built in short iterations. Each one followed the same loop:

1. **Review** the build and list findings: **P0** (must fix before a live demo), **P1** (should fix), and
   **ST-01 to ST-20**, twenty scenario tests that describe behaviour end to end.
2. **Fix** each finding and add a regression test for it.
3. **Run it live** on a MacBook Air M4. Every sentence that misbehaved in the app log became a new test, and the
   whole log was replayed through the old and new code to check that nothing else changed.

Code comments refer to these ids (for example "iteration 2, P0-E" or "iteration 3, item D"), so each rule in the
code can be traced back to the reason it exists.

**Evidence levels used below**

- **LIVE:** seen working on the Mac, in the app log or on screen.
- **TESTED:** covered by automated tests (camera, mic, Ollama, `say` and Chrome are faked in tests; the suite
  refuses all network connections).
- **UNVERIFIED:** built, with no live evidence yet.

Build ids come from `python -m tools.build_id`.

---

## Iteration 1: voice pipeline and the first live runs

- Voice commands v3 to v6: a complexity router (simple asks go to fast rules, long ones to the local model
  planner), a schema-restricted planner, AI-chat prompts (prefill only), Spotify track play, GitHub
  top-repo search, "collect" into Apple Notes, closing apps, message prefill (never sent), and a context resolver
  ("close it", "copy those links", "send it").
- Talkback: JAMES says what it did.
- LIVE: owner key, problems to fixes, Spotify track play and GitHub search, local planner (3.7 s), collect to Notes,
  talkback.
- Defects from the live log became `tests/test_defects_v7.py` (14 tests).

## Iteration 2: hardening (P0-A to P1-F)

| Finding | What changed | Status |
|---|---|---|
| P0-A low confidence | Below 0.5 confidence JAMES shows what it heard and what would run, and waits 12 s for a clear "yes". The model is never asked below 0.5. Stop, cancel and mute stay immediate. | TESTED |
| P0-B intent | Negation ("do not send it"), questions ("explain how to close an app") and quoted text never become actions. Partial plans name each part that was not done. | TESTED |
| P0-C send | Asking ChatGPT or Claude only fills the box (`ASK_AUTO_SEND = False`). "Send it" presses send only in the Chrome tab that holds the exact prompt JAMES prepared. | TESTED; Chrome selectors UNVERIFIED |
| P0-D egress | One gate on every outbound path (`james_core/egress`). The maintenance app and the Lab refuse all consumer web tools; the model must be local. | TESTED |
| P0-E agents | FOREMAN became a loop: fan-out to PAGE, PULSE and LEDGER, then one valid action per round (`page_search`, `page_check`, `ask`, `finish`, `abstain`). | TESTED |
| P0-F microphone | The device is always released; opens, closes and errors are logged; `tools/mic_check`; typed input with `/`. | TESTED |
| P1-A consumer repairs | Generic fixes are labelled "not an approved or OEM procedure". | TESTED |
| P1-B owner | A command is dropped if another hand takes control while it is recorded. | TESTED |
| P1-C markers | `tools/make_marker_sheet` prints the owner key and every pack tag; the owner key is never targeted as a machine. | TESTED |
| P1-D sources | Past jobs are used only if approved, not revoked, same equipment class and recent; memory never decides a cause alone. | TESTED |
| P1-E pack | Pack files are read once, hash-checked and served from memory; a snapshot hash is stored with every job. | TESTED |

Scenario tests ST-01 to ST-20 live in `tests/test_iter2_*.py` (voice, egress, FOREMAN, owner, microphone, pack).
ST-12 (restart) is a deliberate cancellation, not a resume.

## Iteration 3: confirmation, budgets and grounding

| Item | What changed |
|---|---|
| A. Confirmation | Every uncertain action waits for a short, clear "yes" from the same owner, on the same screen, within 12 s. Only "pause" runs straight away. |
| B. Decision origin | Each FOREMAN round records whether the model or the rules decided, and why a fallback happened. |
| C. Coordinator | Described precisely: one coordinator with specialist tools. |
| D. Budgets | 6 rounds, 6 tool calls, 60 s, a cancel flag, and a stop on repeated actions. Late results for an ended job are ignored. |
| E. Egress | Exact host parsing, every redirect re-checked, the model endpoint pinned to localhost. |
| F. Send contract | The tab URL and box text are re-checked at the moment of the click; one attempt, never retried. |
| G. Grounding | Only real manual rows confirm a cause; only numbered step lines become steps, word for word, each with its page. |
| H. Snapshot | A full investigation opens no pack file after start-up. |
| I. Departure | If the hand leaves while talking, the command waits for a "yes". |

Also in this iteration:

- **Neural voice:** Kokoro-82M through `kokoro-onnx`, fully offline, sentence by sentence, with a fallback to the
  macOS voice. The fp16 model was silent on 3 of 54 test sentences, so the full-precision model is used.
- **Voice switch:** on/off in the header (pinch, click or M), remembered between runs.
- Tests: `tests/test_iter3_*.py`, `tests/test_voice_neural.py`.

## Iteration 4: live evidence on the Mac

LIVE on the Mac:

- The FOREMAN loop with the real local model (qwen3-vl:4b): 5 of 5 model decisions accepted, 2 to 3 s per round.
  An early version gave up before checking evidence; after the fix it checked the cause and concluded correctly.
- WARDEN kept the coupling-guard step blocked until lockout and zero energy were confirmed.
- Microphone: 15 of 15 cycles clean, with and without the voice talking.

**A crash found and fixed.** Playing neural audio through PortAudio in the same process as the microphone crashed
on macOS. Each sentence is now played by `afplay` in its own process; PortAudio is used only for the mic.

**Fixes from the live run** (`tests/test_live_0420.py`, 13 tests):

- A model plan searched a site the user never named. Model search and collect steps must now name a site the user
  said.
- "Open ChatGPT on Chrome" opens the website in that browser instead of the desktop app.
- Unknown site names become a web search in the named browser, and JAMES says so.
- Empty brand strings from the vision model are cleaned up; fix steps ask for macOS instructions.
- Long first sentences are split so the voice starts sooner.

**Latencies measured live:** Identify 15.0 s, model fix steps 14.1 s, model plans 4.6 to 8.1 s, rule plans under 1 s.

**Testing change:** `tools/simmac.py` runs the whole suite as if on macOS, which caught three tests that only
failed on a real Mac. Every build now passes both runs.

---

## Still open

1. The VOICE switch, website-in-browser handling and the first-word latency, live on the Mac.
2. Owner key design: the logs show the key is often switched off at the start of a session. Either let a single
   hand drive when only one person is in view, or make re-acquiring the key faster. Not decided.
3. Durable resume after a restart (a restart cancels the job today).
4. Signed industry packs (ed25519).
5. Chrome selectors, media keys, Instagram and WhatsApp prefill: UNVERIFIED.
6. Licence review of the neural voice's `phonemizer` / `espeak-ng` (GPL-3.0) before any commercial distribution.
