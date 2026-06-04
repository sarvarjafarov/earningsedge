# 3-minute hackathon demo video — shot-by-shot script (v2)

**Total runtime:** ~2 min 50 s (cap is 3:00; anything beyond 3:00 is
not evaluated per the official rules).
**Recording tool:** QuickTime, OBS, ScreenStudio — any screen recorder
that exports MP4.
**Hosting (per official rules):** **YouTube or Vimeo only**. Loom is
NOT acceptable per the contest rules. Upload as Unlisted on YouTube
(satisfies the "publicly visible" requirement) and paste the link into
the Devpost submission.
**Resolution:** 1920×1080.
**Audio:** clean mic, no background music during dialogue.
**Language:** English (rules require English or English subtitles).

Open the live URL in a fresh Chrome window so the cursor isn't covered
by browser chrome. Have a second Chrome tab pre-loaded with a real
NVDA-related YouTube clip (analyst recap, earnings replay) — we'll
switch to it at 1:50.

---

## 0:00 — 0:30 — The objection, met head-on (no UI yet)

> **Voiceover, over B-roll: clock striking 5 PM ET on the NYSE floor,
> then cutting to a phone screen with a Twitter feed scrolling,
> someone at a dinner table glancing at the phone, a brokerage app
> showing the open the next morning with a red P/L:**
>
> "Here's the problem with retail investors and earnings calls.
>
> The calls happen at 5 PM, after the market closes. Twenty-five
> percent of US equity volume is retail. They aren't on the call.
> They don't have a Bloomberg. By the time the market opens at 9:30
> the next morning, Twitter has formed their opinion for them. They
> buy at the open based on a 280-character take, and they lose money
> on the fade.
>
> **EarningsEdge is not trying to beat Wall Street on speed.** That
> race is over. What we do is **give retail the depth Wall Street has
> at retail's natural decision time — breakfast Wednesday, before the
> bell.**"

**B-roll cuts to match:**
- 0:00–0:08 NYSE closing bell + clock striking 5 PM
- 0:08–0:18 Twitter feed scrolling fast on a phone, finger hovering
- 0:18–0:25 brokerage app red P/L
- 0:25–0:30 EarningsEdge cockpit fades in, **Heroku URL visible** in
  browser bar

---

## 0:30 — 1:00 — The five named investors (this is the headline)

**Cut to the demo URL. Type `NVDA` → press LOAD COMPANY. The
ChairmanADKPanel above the tab switch auto-fires and starts running.**

> "Watch what happens when I load NVDA. Above the cockpit, the Google
> Cloud Agent Builder layer is already running. It's not just an
> AI committee — it's **five specific investor lenses**, each modeled
> on a recognizable public investor's published philosophy:
>
> **Cathie Wood** — five-year disruptive-innovation lens. She sees
> AI infrastructure compounding through 2030.
>
> **Michael Burry** — forensic-accounting bear. He finds the
> contradiction the bulls are missing.
>
> **Stan Druckenmiller** — concentrated macro bets, asymmetric
> 6-to-12-month setups.
>
> **Jim Cramer** — rapid headline reaction, narrative pivots.
>
> **Howard Marks** — cycle-position framework. Is the price
> compensating us for the risk we're taking?
>
> You don't get to ask Howard Marks what he thinks about NVDA. Now
> you do. Same Gemini 3 brain across all five, different
> instructional prompts, distinct voices."

**While speaking, hover over the `Run for NVDA` button. Then click
into the tool-call trace `details` expand:**

> "Watch the agent fire **find_similar_past_verdict** — that's a
> semantic search against MongoDB Atlas Vector Search of every prior
> committee verdict we've ever written. The memory is the headline
> feature, not a side effect."

---

## 1:00 — 1:30 — The memory closing the loop (zoom in on the response)

**The agent's response renders. Zoom into the part where it cites a
prior verdict.**

> "Look at the first line of the synthesis. The agent says:
>
> 'This rhymes with our NVDA Q1 2024 verdict — same compute-capacity
> language preceded a 6.2 percent drop in seven days.'
>
> That's not in the training data. That's in our MongoDB Atlas
> verdict store, indexed by `gemini-embedding-001` vectors, retrieved
> by a `$vectorSearch` aggregation, and quoted verbatim by the
> Chairman in today's synthesis.
>
> **The agent remembers what it said last quarter. The next call will
> remember what it says today.** Every session adds to the corpus.
> Twitter cannot do this. CNBC cannot do this."

**Hover over the structured verdict fields:**
- Action: Hold
- Confidence: MEDIUM
- Named dissent: from Burry on the compute-capacity language
- Paper trade: queued (not executed)

---

## 1:30 — 2:00 — The cockpit (existing committee + paper trade)

**Click "Committee" tab. Pan across the four-column committee view
that the legacy orchestrator populates.**

> "Below the agent panel is the production cockpit. Five legacy
> specialist agents — fundamentals, peers, analyst consensus, news
> sentiment, technicals — populate the dashboard in parallel. Same
> tools the named-investor lenses are calling, just rendered
> differently. The committee score is weighted by confidence with
> hysteresis.
>
> Every coverage call persists to MongoDB Atlas as a session
> document. Every paper trade lands in the `trades` collection. The
> Chairman writes verdicts to the `verdicts` collection with their
> embedding vectors. The full corpus rebuilds the memory loop on
> every restart."

---

## 2:00 — 2:30 — Live audio (optional — only if Gemini Live is up)

**Click "Listen live". In the share dialog:**

1. Switch to the second Chrome tab with NVDA-related audio
2. Tick **Share tab audio**
3. Click Share

**Cut back to the cockpit. Audio streams.**

> "When you want the live path, drop in real audio. Gemini 3.1 Live
> transcribes with speaker diarization and twelve-topic routing.
> Each line gets scored against the five named-investor lenses as
> it lands. But — and this is the point — **the live path is
> optional**. The night-shift agent runs against any uploaded clip
> or any URL. The user reads the verdict in the morning, not at the
> 5 PM live call."

**If Gemini Live is unavailable on the day of recording, SKIP this
section entirely and extend section 2:30 instead. The agent path
without live audio is the more important demo anyway.**

---

## 2:30 — 2:50 — Close on the architecture and the line

**Cut to the Mermaid architecture diagram from the README — the one
showing the five named-investor sub-agents plus Atlas Vector Search.**

> "One stack: Google Cloud Agent Builder over Gemini 3, five
> named-investor LlmAgents, MongoDB Atlas Vector Search for the
> memory of every verdict, MongoDB MCP for the rest of the
> persistence, Alpaca paper for execution.
>
> Same code path the retail investor uses. Same code path the judge
> can curl. Open-source MIT. Live demo URL in the description.
>
> **EarningsEdge is the night-shift analyst that sleeps through
> earnings calls so you don't have to.**"

**End card:**
- 🔗 Live demo: `https://earningsedge-3391b61f61d9.herokuapp.com`
- 🔗 GitHub: `github.com/sarvarjafarov/earningsedge`
- 🏷️ MongoDB partner track · Financial Services theme

---

## Production checklist

- [ ] Heroku URL warm before recording (hit `/health` and `/api/mcp/status`)
- [ ] Pre-load the demo URL and a ticker in browser so coverage is warm
- [ ] Pre-load the second Chrome tab with an NVDA-related YouTube clip
      (analyst recap, earnings replay) — un-pause it just before 2:00
- [ ] Mic test, no background noise
- [ ] Hide bookmarks bar + notifications during recording
- [ ] Verify `/api/gemini/health` returns `available: true` (or skip
      the live audio segment entirely)
- [ ] Have a screenshot of the architecture diagram open in a separate
      tab for the closing shot
- [ ] Upload to YouTube unlisted, paste link into Devpost

## Tagline options for the video title / Devpost

- **"Sleep through earnings calls. Wake up with conviction."** (chosen)
- "Five named investors. One earnings call. Zero hot takes."
- "The night-shift analyst for retail investors."
- "EarningsEdge — what Twitter can't tell you about earnings calls."
