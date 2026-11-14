# 🔥 THE MAID WHO BOUGHT THE EMPIRE
### A Hooked, High-Retention Micro-Drama Series — Series Bible

> **Logline:** A humiliated maid is fired on camera — then buys the company that fired her.
> **Genre:** Billionaire Revenge (proven viral engine: public humiliation → secret power reveal)
> **Loop question (drives all 10 episodes):** *Who really owns the empire — and who burned the house down?*
> **Format:** 60–90s vertical episodes, 10 episodes/season, cliffhanger every episode
> **Tone:** Shameless, fast, revenge-satisfying. Rich people behaving badly. Poor girl winning.

---

## Why this series hooks (retention design)

| Retention lever | How it's used |
|---|---|
| **3-second hook** | Ep 1 opens mid-slap at a gala. No title card. Scroller stops or swipes. |
| **Open loop** | "Why does her phone say she owns 51%?" — posed in 10s, answered in Ep 2. |
| **Pattern interrupt** | New shock every ~5s: slap → fired → phone → kneel → takeover → fire question. |
| **Escalation** | Humiliation → shock → revenge satisfaction → bigger mystery. Never flat. |
| **Payoff + new mystery** | Each episode closes one loop (satisfaction) and opens a bigger one. |
| **Cliffhanger** | Every episode ends on a reveal/question. Never on resolution. |
| **Hateable villain** | Vivian is hateable in 5 seconds (slaps a maid on camera). |

---

## Characters (consistent across episodes — paste into generator)

**Lena Cruz, 26 — Protagonist.** Orphan maid. Secret: true heiress of the Vega Empire, presumed dead after a house fire 15 years ago. Goal: take back the empire and expose the arsonist. Visual: long black hair, sharp dark eyes, maid uniform in Ep 1 → black power suit after takeover. Signature line: *"You fired the wrong maid."*

**Vivian Vega, 48 — Antagonist.** Stepmother. Framed Lena for theft, set the fire, stole the empire. Visual: blonde bob, red lipstick, designer gowns, diamond necklace. Hateable in 5 seconds. Signature line: *"You're fired. Thief!"*

**Marcus Chen, 32 — CEO / Ally.** Knows Lena's secret. Loyal. Visual: navy suit, calm, kneels in Ep 1. Signature line: *"Ma'am Lena, welcome back."*

**Sofia Reyes, 24 — Best friend / witness.** Fellow maid. Comic relief + audience surrogate. Visual: curly hair, maid uniform, phone always in hand (records everything).

**Diego Vega, 28 — Half-brother.** Vivian's son. Torn between mother and sister. Holds the witness secret. Visual: leather jacket, haunted eyes.

**Inspector Rosa, 40 — Police.** Holds the original fire report. Visual: police uniform, case folder.

---

## Canon cast kit (identity lock)

Every main character has two artefacts in Drive:

- a **turnaround sheet** (front / three-quarter / profile) — the human-readable canon
- a **single-figure portrait** — what actually goes to the video model

Portraits are the default reference. A sheet puts three faces on one canvas, and the model can composite a duplicate person into a shot; a single-figure crop cannot.

| Character | Costume window | Portrait | Sheet |
|---|---|---|---|
| **Lena Cruz** | Ep 1 — maid uniform | `lena_maid_portrait.jpg` | `lena_character_sheet.png` |
| **Lena Cruz** | Ep 2–10 — black power suit | `lena_suit_portrait.jpg` | `lena_suit_sheet.png` |
| **Vivian Vega** | Ep 1–10 — black gown + diamonds | `vivian_portrait.jpg` | `vivian_character_sheet.png` |
| **Marcus Chen** | Ep 1–10 — navy three-piece suit | `marcus_portrait.jpg` | `marcus_character_sheet.png` |

**Locked appearances.** Lena: 26, long black hair in a neat low bun with loose temple strands, sharp dark brown eyes, strong brows, fair olive skin, high cheekbones, 5'6". Vivian: 48, blonde jaw-length bob, pale blue eyes, sculpted cheekbones, matte red lipstick. Marcus: 32, short fade, warm brown eyes, clean-shaven, 6'0".

### Costume windows are enforced in code

The sheet and the prompt resolve from the same episode number, so they cannot disagree:

| Episode | Lena's reference | Lena's costume text |
|---|---|---|
| 1 | `lena_maid_portrait.jpg` | black maid dress, scalloped lace apron |
| 2–10 | `lena_suit_portrait.jpg` | black power suit, white silk blouse |

```bash
cd server
python run_sample.py                            # Ep 1, Lena, maid — fully locked
python run_sample.py --episode 3                # Ep 3, power suit
python run_sample.py --characters lena,vivian   # two reference assets
python run_sample.py --characters none          # disable identity locking
```

### Regenerating portraits

If a sheet changes, re-cut the portraits instead of cropping by hand:

```bash
python server/tools/make_portraits.py --figure 0   # 0=front, 1=three-quarter, 2=profile
```

It detects figures by column texture (a studio backdrop is smooth, a body is not), clamps each box to the midpoint between neighbours so a neighbour's hand cannot bleed in, and fits the crop to a2e's reference limits.

### Reference-image limits (why the crops are 600 px wide)

a2e validates reference stills at **600–2400 px per side, ≤36 MP, JPG/PNG/WEBP/GIF**. The validator sits in the web UI, but the API enforces the same rule *after* submission — so an out-of-spec portrait burns coins before you find out. The first cut of these portraits was 364–480 px on the short side and would have been rejected.

| Portrait | Delivered size |
|---|---|
| `lena_maid_portrait.jpg` | 600×2009 |
| `lena_suit_portrait.jpg` | 600×2251 |
| `vivian_portrait.jpg` | 600×2400 |
| `marcus_portrait.jpg` | 669×941 (native, no upscale) |

A standing figure is naturally narrow, and the two limits collide: at 1:4, a 600 px short side is exactly a 2400 px long side. Anything narrower than 1:4 cannot satisfy both, so the cropper trims to 1:4 before scaling instead of emitting a rejected file. `tools/reference_spec.py` holds the numbers, and `GET /api/readiness` reports `reference size:<character>` so this cannot regress silently.

---

## 10-Episode Arc

### Ep 1 — "You Fired the Wrong Maid"
- **Hook (0–3s):** Slap echoes across a gala. Vivian screams: *"You're fired! Thief!"* On-screen text: *"She was fired for stealing. She wasn't the thief."*
- **Open loop:** Why does Lena's phone say *"Ma'am, you now own 51% of Vega Group"*?
- **Beats:** Slap → fired on camera → phone rings → CEO kneels → Lena takes the company → Vivian frozen.
- **Twist:** Vivian planted the necklace herself to frame Lena.
- **Payoff:** Lena buys the company. One loop closed.
- **Cliffhanger (final 3s):** Lena stares at Vivian: *"Now... let's talk about who burned my house down."* **CUT TO BLACK.**

### Ep 2 — "The Necklace Was in Her Bag"
- **Hook:** Security footage plays on the gala screen — Vivian's hand slips the necklace into Lena's bag.
- **Open loop:** Who signed the fire report — and why is the signature familiar?
- **Beats:** Footage exposed → Vivian humiliated → Lena reinstated as owner → Vivian begs → Marcus slides fire report across table.
- **Twist:** The fire was arson, not an accident.
- **Payoff:** Vivian publicly humiliated. Satisfaction.
- **Cliffhanger:** Lena opens the report. Her hands shake. *"The signature... it's yours."* **CUT TO BLACK.**

### Ep 3 — "The Fire Report"
- **Hook:** Vivian burns the report in front of Lena. *"There was no fire. There was an accident."*
- **Open loop:** Diego saw someone at the house that night. Who?
- **Beats:** Report burned → Lena confronts Vivian → Vivian denies everything → Diego watches silently → Diego whispers to Lena.
- **Twist:** Diego was 13 that night. He saw a woman at the house.
- **Payoff:** Lena gets a witness.
- **Cliffhanger:** Diego: *"I saw her. But if I talk... she'll kill you too."* **CUT TO BLACK.**

### Ep 4 — "The Witness"
- **Hook:** Vivian threatens Diego in the parking garage: *"Talk, and you lose everything."*
- **Open loop:** Diego's phone was recording. Did it catch her?
- **Beats:** Threat → Diego scared → Sofia's phone footage → Lena protects Diego → Vivian panics → Vivian calls someone: *"Handle it."*
- **Twist:** Vivian is not working alone.
- **Payoff:** Diego testifies. Vivian cornered.
- **Cliffhanger:** Vivian on the phone: *"She survived the fire. Make sure she doesn't survive this."* **CUT TO BLACK.**

### Ep 5 — "The DNA Test"
- **Hook:** Vivian announces on live TV: *"Lena Cruz is NOT my daughter. DNA proves it."*
- **Open loop:** Two DNA tests say opposite things. Which one is forged?
- **Beats:** Live accusation → Lena humiliated again → Marcus reveals second test → 100% Vega heir → Vivian's test exposed as fake → Vivian's lab technician flees.
- **Twist:** Vivian forged the test. The technician is her lover.
- **Payoff:** Lena proven heir. Vivian's lie destroyed.
- **Cliffhanger:** The technician runs. Sofia chases. A car door slams. *"He's getting away!"* **CUT TO BLACK.**

### Ep 6 — "The Will"
- **Hook:** Grandfather's will is read aloud: *"The empire goes to Lena Cruz."* Vivian tears it in half.
- **Open loop:** There's a second will. Hidden in the burned house.
- **Beats:** Will read → Vivian tears it → Lena calm → Marcus reveals second will → Vivian laughs: *"That house is ashes."* → Lena: *"Then I'll dig through the ashes."*
- **Twist:** The second will names a co-conspirator.
- **Payoff:** Lena legally heir.
- **Cliffhanger:** Lena at the burned house at night. A safe door creaks open. It's empty — except a photo. **CUT TO BLACK.**

### Ep 7 — "Return to the Ashes"
- **Hook:** The photo: Vivian with an unknown man in front of the house, the night of the fire.
- **Open loop:** Who is the man? And why is he holding a petrol can?
- **Beats:** Photo found → Lena shocked → Marcus identifies man → Vivian's lover, co-arsonist → Vivian tries to buy Lena off → Lena refuses → Vivian threatens.
- **Twist:** The man is still alive. And still working for Vivian.
- **Payoff:** Lena has proof of conspiracy.
- **Cliffhanger:** The man's phone rings in the dark. He answers: *"She found the photo."* **CUT TO BLACK.**

### Ep 8 — "The Unknown Man"
- **Hook:** The man steps out of the shadows. It's Inspector Rosa's husband.
- **Open loop:** Rosa holds the original fire report. Is she protecting him?
- **Beats:** Identity revealed → Lena confronts Rosa → Rosa breaks down → Rosa confesses she was blackmailed → Vivian's leverage exposed → Rosa gives Lena the original report.
- **Twist:** Rosa was blackmailed with her daughter's life.
- **Payoff:** Lena gets the original arson report.
- **Cliffhanger:** Vivian's car screeches to a halt. She rolls down the window, smiling: *"Lena, darling. Let's talk."* **CUT TO BLACK.**

### Ep 9 — "The Betrayal"
- **Hook:** Vivian offers Lena a deal in her car. Lena refuses. Vivian's smile drops.
- **Open loop:** Vivian just ordered a hit. Will Lena survive?
- **Beats:** Deal refused → Vivian orders hit → Lena's car brakes fail → Sofia records everything → Lena survives → police swarm → Vivian arrested on camera.
- **Twist:** Lena knew about the hit. She let it happen — to catch Vivian on camera.
- **Payoff:** Vivian arrested. Revenge complete.
- **Cliffhanger:** Vivian in handcuffs screams: *"You think it's over? He'll never let you win!"* **CUT TO BLACK.**

### Ep 10 — "The Heir" (Season Finale)
- **Hook:** Lena crowned CEO. The boardroom kneels — again, but this time in respect.
- **Open loop:** Who is "he"? The letter arrives.
- **Beats:** Coronation → empire restored → Lena forgives Diego → Sofia celebrates → letter delivered → Lena reads it → her face changes.
- **Twist:** The letter: *"The fire wasn't Vivian's idea. It was mine. — M."*
- **Payoff:** Lena takes the empire. Justice served.
- **Cliffhanger (Season 2 hook):** Lena looks up. Marcus is gone from his chair. The door closes. *"Marcus?"* **CUT TO BLACK. TO BE CONTINUED.**

---

## How to generate Ep 1 (copy-paste)

**Option A — Idea to Video (Hooked Series Mode ON, Genre: Billionaire Revenge, Ep 1):**
```
THE MAID WHO BOUGHT THE EMPIRE — Ep 1: A maid is slapped and fired on camera at a gala for stealing a necklace she didn't steal. Her phone rings: 'Ma'am, acquisition complete. You now own 51% of Vega Group.' The CEO kneels. She takes the company. Cliffhanger: 'Now... let's talk about who burned my house down.'
```
Requirements: `Vertical 9:16 feel, fast cuts, hateable villain, revenge payoff in Ep 1, end on cliffhanger.`

**Option B — Script to Video (Hooked Series Mode ON):**
Paste `series/episode_01_script.txt` into the Script field.

**For Ep 2+:** Keep Hooked Series Mode ON, set Episode # to 2, 3, 4..., paste the previous episode's `story.txt` / bible into Series Bible field for continuity.

---

## Posting strategy (retention outside the video)

- **Title:** `She Was Fired as a Maid. Then She Bought the Company. 😱 #microdrama`
- **Thumbnail/first frame:** The slap + on-screen text. Must work at 1-second glance.
- **Captions:** Burn the hook line as on-screen text in the first 3 seconds.
- **End screen:** "Ep 2 tomorrow — who signed the fire report?" (poses the loop).
- **Comment bait:** Pin: *"Who do you think burned the house down? 👀"*
- **Cadence:** Daily episodes. Cliffhangers need next-day payoff or viewers churn.

---

## 5 alternate series (same engine, different genre)

1. **Rejected Mate** — Alpha rejects fated mate in public; her eyes turn gold. Loop: *Why did he reject her — and what is she hiding?*
2. **Time-Loop Murder** — She dies at 11:59pm, wakes at 8am. Loop: *Who kills her — and why does the day reset?*
3. **Fake Heiress** — Impostor exposed... as the true heir. Loop: *Which heiress is real — and who forged the DNA test?*
4. **AI Husband** — Perfect husband is AI, but knows a secret no human should know. Loop: *Is he AI, human, or something else?*
5. **Murder Mystery** — Victim names the killer, then dies. Loop: *Who did it — and why does the detective hide a clue?*

All 5 are one-click presets in Hooked Series Mode (Genre Engine).
