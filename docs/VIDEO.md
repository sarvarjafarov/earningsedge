# 3-minute hackathon demo video — shot-by-shot script

**Total runtime:** 2 min 50 s. **Tool:** Loom / QuickTime / OBS.
**Resolution:** 1920×1080. **Audio:** clean mic, no background music
during dialogue.

Open the demo URL in a fresh Chrome window so the cursor isn't covered
by browser chrome. Have a second Chrome tab pre-loaded with a real
NVDA earnings webcast or news clip; we'll switch to it at 1:30.

---

## 0:00 — 0:25 — The problem (no UI yet, talking head + B-roll)

> **Voiceover (over a montage of: Twitter scrolling, "AI tells you to
> sell" TikToks, panicked 9:30 AM brokerage app screenshots):**
>
> "Tuesday at 5 PM, a Fortune 500 CEO gives an earnings call. The
> stock will gap in 16 hours when the market opens Wednesday. Twenty-five
> percent of US equity volume is retail investors who will use those
> 16 hours to doom-scroll Twitter and watch ninety-second TikTok recaps.
> Then they'll panic-trade the open.
>
> EarningsEdge gives that retail investor an institutional analyst on
> call. Three Gemini-3 agents debate the audio in real time, search
> memory of every prior committee verdict, and let them execute a
> disciplined paper trade with one tap."

**B-roll cuts to match:**
- 0:00–0:08 Twitter feed scrolling fast
- 0:08–0:15 TikTok-style finance influencer
- 0:15–0:22 brokerage app with red numbers and a finger hovering "sell"
- 0:22–0:25 EarningsEdge cockpit fades in

---

## 0:25 — 0:50 — Load a ticker, watch the committee fan out (live UI)

**Cut to demo URL. Type `NVDA` → press LOAD COMPANY.**

> "I drop NVDA into the cockpit. In ten seconds five specialist agents
> have fanned out — analyst consensus, peers, news, macro, technicals
> — and the committee weights their score blocks into a single
> coverage view."

**While the panels populate:**
- Hover over the analyst consensus panel — "93 of 100, strong buy"
- Hover over the news sentiment — "rolling 7-day overall positive"
- Hover over the committee verdict — "Add, MEDIUM confidence"

---

## 0:50 — 1:30 — The Agent Builder layer auto-fires (this is the headline)

**The ChairmanADKPanel at the top is already running. Zoom into it.**

> "Above the cockpit, the Google Cloud Agent Builder layer has already
> started running. This is the hackathon's required path —
> Gemini 3 inside an LlmAgent with three sub-agents under it: Bull,
> Bear, Quant. They run in parallel, each with its own focused tool
> set."

**Click into the response. Highlight specific lines as the camera pans:**

> "Watch what the agent does first: it called
> `find_similar_past_verdict`. That's a vector-search query against
> our MongoDB Atlas memory of every prior committee decision. It
> finds Q1 2024, when the CFO's compute-capacity language got
> cautious. Our Bear sub-agent flagged it then. The stock dropped
> 6.2% in seven days."

**Expand the Tool-call trace `details`. Scroll through the names:**

> "Eleven tools — fundamentals, peers, analyst consensus, news
> sentiment, paper account, paper positions, draft trade,
> find similar past verdict, remember verdict. Every figure in the
> response is tied to one of those calls. The model invents no
> numbers."

---

## 1:30 — 2:05 — Live audio via Gemini Live (switch tabs, share audio)

**Click "Listen live". In the share dialog:**

1. Switch to the second Chrome tab playing an NVDA-related clip
2. Tick **Share tab audio**
3. Click Share

**Cut back to the cockpit. Audio is now streaming.**

> "I drop in real audio. Gemini 3.1 Live transcribes — speaker
> diarization, twelve-topic routing — and the agents score each line
> as it lands. When something matches a pattern from the memory store,
> it's flagged inline."

**Wait for ~10 s of transcript to land, then:**

---

## 2:05 — 2:30 — Voice Q&A, paper trade

**Click the mic icon. Speak:**

> "What's the bear case here, in one sentence?"

**The Chairman replies in voice. While the audio plays, the answer
also renders as text.**

> "I ask in voice. The Chairman routes the question to the bear
> sub-agent and replies with text and Gemini 2.5 TTS audio."

**As soon as the reply is rendered, click the paper trade approval
button on the impact panel.**

> "I approve the paper trade. Alpaca executes against a paper book —
> the trade lands in MongoDB Atlas alongside the verdict that
> motivated it. Both are now part of the agent's memory for the
> next call."

---

## 2:30 — 2:50 — Close on the architecture

**Cut to the Mermaid architecture diagram from the README — the one
that shows the multi-agent + MCP shape.**

> "One screen: Google Cloud Agent Builder over Gemini 3, MongoDB
> Atlas Vector Search for memory, all live — same code path the
> retail investor uses, same code path the judge can curl. Open-source
> MIT, public repo, live URL in the description. That's EarningsEdge."

**End card: GitHub URL + live demo URL + "MongoDB partner track".**

---

## Production checklist

- [ ] Pre-load demo URL with a real ticker so coverage is warm
- [ ] Pre-load the second Chrome tab with NVDA earnings audio (or any
      financial YouTube clip) — un-pause it just before the 1:30 mark
- [ ] Test the mic is recording cleanly
- [ ] Hide bookmarks bar and notifications during recording
- [ ] Verify `/api/gemini/health` returns `available: true` before
      hitting record
- [ ] Have a screenshot of the architecture diagram open in a
      separate tab for the closing shot
- [ ] Upload to YouTube unlisted, paste the link into Devpost
