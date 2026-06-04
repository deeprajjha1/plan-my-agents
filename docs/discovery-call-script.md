# Discovery Call Script — 15-Minute Format

> One job: **understand their reality, not pitch yours.** You speak ~20%, they speak ~80%. Every minute you spend pitching is a minute of insight you didn't capture.

> Based on Rob Fitzpatrick's *The Mom Test*. If you've never read it, read chapters 1–4 before your first call. It's 90 minutes well spent.

---

## 1. Pre-call (5 minutes before)

- [ ] Open their LinkedIn — last 3 posts, current role, prior roles
- [ ] Open their company — main product, ICP, recent funding/announcements
- [ ] Open their company's job board — RevOps roles open?
- [ ] Open the tracking sheet — log "call_started_at"
- [ ] Open a fresh Google Doc for note-taking, titled `{date}-{first_name}-{company}`
- [ ] Have the 7 questions in front of you (next section)
- [ ] Mute Slack/email notifications

---

## 2. The opening (60–90 seconds — hit these beats verbatim)

> *"Hey {first_name}, thanks for the time. Quick framing: I'm spending a couple weeks talking to RevOps people at SaaS companies your size to understand how the enrichment workflow actually works in practice. I'm not selling anything today — I'm researching before I build, so honest answers help me way more than polite ones. Mind if I record this for my own notes? I'll share back what I learn from all the conversations."*

Then ask permission for the recording. Then go.

**Critical rules during the call:**
- ❌ Don't pitch
- ❌ Don't mention pricing
- ❌ Don't say "I think we'd build it like..."
- ❌ Don't agree with everything they say (you'll get false positives)
- ✅ Ask follow-ups: *"Tell me more about that"* / *"Why?"* / *"What did you do then?"*
- ✅ Embrace silence — don't fill it; let them think
- ✅ When they say something painful, dig deeper — *"How often does that happen?"* / *"Last time it happened, what did you do?"*

---

## 3. The 7 core questions (in order — don't skip)

### Q1: Walk me through last week
> *"Walk me through the last week or two — what did you actually spend time on, day to day?"*

**What you're listening for:** their real workflow, not the org chart version. The pains usually surface unprompted in the first 3 minutes. Note exact phrases they use ("data hygiene," "campaign loading," "list pulling").

---

### Q2: The most annoying part
> *"What's the single most annoying / boring / soul-sucking part of your job?"*

**What you're listening for:** emotional intensity. If they laugh nervously or get visibly frustrated, you've hit it. If they shrug, the pain isn't acute enough.

---

### Q3: Past behavior on the specific pain (Mom Test gold)
> *"The last time you had to {build a list of N companies / enrich N records / do thing X} — walk me through what you actually did. Tools, time, who else was involved."*

**What you're listening for:**
- Concrete tools mentioned (Clay, Apollo, ZoomInfo, scripts, interns, Excel)
- Time spent (hours? days?)
- Who else was pulled in (suggests team-level pain, bigger budget)
- **Did they hack a workaround?** (Strongest signal of all — means budget exists)

---

### Q4: What didn't work
> *"What have you tried that didn't work? Tools you bought and stopped using, or things you built in-house and abandoned?"*

**What you're listening for:** signals about *why* solutions fail in their context. Often "the data was inaccurate" or "it took too long to set up" or "we couldn't get the team to use it." These are the "must avoid" patterns for your product.

---

### Q5: Cost (in $ AND time)
> *"Roughly, how much does this cost you today — both in tooling spend and in your time or your team's time?"*

**What you're listening for:**
- Tooling budget (often $500–$5K/mo for enrichment alone)
- Time spent (hours/week of theirs and team's)
- Hidden costs (failed campaigns from bad data, deals lost, reputation damage from cold email bounces)

If they can quote the cost from memory, the pain is real and budgeted. If they hand-wave, it's not yet a budgeted pain.

---

### Q6: The "if magic" question (carefully)
> *"If you could just describe your target list in plain English — 'find me 200 EU HR-tech CTOs at SaaS $5–50M ARR with verified emails and recent product news' — and have it executed by the right specialist tools automatically with cost capped, would you actually use that?"*

**Listen for the response shape:**
- 🟢 *"Yes — when can I have it?"* → strongest possible signal
- 🟢 *"Yes if {specific condition}"* → strong, captures their must-have
- 🟡 *"Maybe — depends on accuracy"* → real but conditional; probe more
- 🔴 *"Sure, sounds nice"* → polite no, ignore
- 🔴 *"Why would I switch from {current tool}?"* → friction is high; this isn't your customer (yet)

---

### Q7: Intros + design partner ask
> *"Two more things: First, who else on your team or in your network feels this pain — could you intro me to 1–2 people? Second — if I built a v0 of this in the next ~2 months, would you be willing to be a design partner? Free for you, your honest feedback for me."*

**What you're listening for:**
- Willingness to intro = social proof of pain
- Willingness to be design partner = the verbal commit you need (count it for your Day 14 gate)
- Hesitation on either = soft signal it's not painful enough

---

## 4. Things to NEVER say on a discovery call

| ❌ Don't say | Why | ✅ Say instead |
|---|---|---|
| *"Would you pay $X/month for this?"* | They lie politely | *"Last time you bought a tool like this, what did you pay and what made you commit?"* |
| *"Our product is going to..."* | You're pitching | *"Walk me through how you do this today."* |
| *"That's a great idea!"* (when they suggest a feature) | False positive; you'll build wrong thing | *"Interesting — when did you last need that specifically?"* |
| *"Don't you think..."* | Leading question | *"What do you think about..."* |
| *"We can do that."* | Premature commitment | *"Tell me more about that need — has it actually come up?"* |
| *"Anyone else doing what we're doing?"* | Looks insecure | Just listen for which competitors they mention unprompted |

---

## 5. The closing (last 90 seconds)

> *"This was super helpful — thank you. Quick recap of what I heard: {1-sentence summary of their core pain in their words}. Did I get that right?"*

(They'll either confirm or correct — the correction is the most valuable insight of the entire call.)

> *"Two asks before we wrap: (1) any 1–2 people in your network you'd suggest I talk to? (2) Would you want to be a design partner when I have something working in 6–8 weeks?"*

(Wait for both answers. Don't over-explain.)

> *"Last thing — I'll write up a summary of what I'm learning across all these calls and send it to you next month. No pitch, just the patterns. Sound good?"*

(This earns the right to follow up. Always honor it.)

---

## 6. Post-call (within 30 minutes)

- [ ] Save the recording (Granola, Otter, Fireflies, or just Zoom local recording)
- [ ] Fill in the tracking sheet:
  - `call_outcome` = strong-yes / soft-yes / no
  - `design_partner_commit` = y/n
  - `reply_summary` = 1-sentence pain in their words
- [ ] Write a 3-bullet summary at the top of the call notes doc:
  - **Their #1 pain (in their words)**
  - **What they've tried that didn't work**
  - **Whether they'd be a design partner — explicit y/n with reason**
- [ ] Send the follow-up email (template below) within 2 hours

---

## 7. Post-call follow-up email template

> **Subject:** Thanks for the call — quick recap

> Hi {first_name},
>
> Genuinely appreciate the time. Quick recap of what I heard so I can confirm I got it right:
>
> 1. **Your biggest pain is:** {one sentence in their words}
> 2. **You've tried:** {tool/approach} — but it didn't work because {reason}
> 3. **You'd find it valuable if:** {specific must-have they mentioned}
>
> Did I capture that correctly? If anything's off, please correct me — I'd rather build the right thing.
>
> {if they said yes to design partner:}
> Will keep you posted on the v0 — targeting working software by {date 6–8 weeks out}. Will reach out when I'm ready for you to test it.
>
> {if they offered intros:}
> Also — would love to talk to {names they mentioned} if you're open to a quick intro. Happy to send a pre-written intro email so it's zero effort for you.
>
> Thanks again,
> {your_name}

---

## 8. Signals matrix — when to count a call as a "yes"

| Signal | Weight |
|---|---|
| They've already hacked a solution (Excel, intern, Zapier, Clay, custom script) | 🟢 +3 |
| They quoted a specific $ or hours cost from memory | 🟢 +2 |
| They got visibly emotional / frustrated talking about it | 🟢 +2 |
| They asked "when can I have this?" before you described any solution | 🟢 +3 |
| They volunteered an intro to 1+ people | 🟢 +2 |
| They explicitly committed to design partner | 🟢 +3 |
| They named 2+ specific tools they tried | 🟡 +1 |
| They said "interesting" / "neat" without specifics | 🔴 -1 |
| They asked about price (and didn't follow up after answer) | 🔴 -1 |
| They said "let me think about it and get back to you" | 🔴 -2 |
| They tried to redirect to "the person who handles that" without intro-ing | 🔴 -2 |

**Score >= +5 = strong yes, count toward design partner gate.**
**Score 0 to +4 = soft signal, follow up in 2 weeks with v0 demo.**
**Score < 0 = not your customer right now, archive.**

---

## 9. Patterns to look for after 5+ calls

After your first 5 calls, sit down and look for repeated phrases. The patterns that matter:

- **Same pain words used by 3+ people** → real category-level pain (validated)
- **Same competitor mentioned by 3+ people** → that's your real competition (not what you guessed)
- **Same workaround mentioned by 3+ people** → your "stop doing this" wedge
- **Same must-have mentioned by 3+ people** → your v0 must include this
- **Same blocker mentioned by 3+ people** → the reason your product needs to address X (e.g., "we'd need GDPR compliance from day 1")

If you don't see strong repetition by call 5, **the vertical might be wrong**. Reassess before continuing.

---

## 10. Doc template for each call

Copy this for every call:

```markdown
# {date} — {first_name} {last_name} ({title} @ {company})

## Top 3 takeaways
- **#1 pain:**
- **Tried but failed:**
- **Design partner commit?** (y/n + reason)

## Stack they use
- CRM:
- Enrichment tools:
- Other relevant:

## Pain phrases (verbatim quotes)
-
-
-

## Specific moments to remember
- Got visibly frustrated when talking about __
- Mentioned hacking __ themselves
- Asked "when can I have this?" at minute __

## Action items
- [ ] Send follow-up email
- [ ] Send intro emails to: ___, ___
- [ ] Add to design partner list: y/n
- [ ] Add to "Pro tier API" early-access list: y/n

## Score (from signals matrix above)
+__ → strong yes / soft signal / not customer
```
