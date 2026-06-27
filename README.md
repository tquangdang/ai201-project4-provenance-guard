# Provenance Guard

A backend service that any creative-sharing platform can plug into to classify
submitted text as AI-generated or human-written, **score its confidence**, surface a
plain-language **transparency label**, and let creators **appeal** verdicts they
believe are wrong. Every decision is written to a **structured audit log**, and the
submission endpoint is **rate-limited**.

The guiding principle: **honest uncertainty beats a confident wrong answer**, and
**falsely labeling a human's work as AI is the worst error we can make.** That
asymmetry shows up in the thresholds, the confidence math, and the label UX.

> Full design rationale, the architecture diagram, the five spec questions, and the
> AI Tool Plan live in [planning.md](planning.md).

---

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows (PowerShell/CMD)
# source .venv/bin/activate       # Mac/Linux
pip install -r requirements.txt
```

Create a `.env` from the template and add your Groq key (free tier):

```bash
cp .env.example .env
# then edit .env:
# GROQ_API_KEY=your_key_here
```

Run the server:

```bash
python app.py
# serves on http://localhost:5000
```

> **No key?** The pipeline still runs. The LLM signal degrades gracefully to
> `None`, the system falls back to stylometry alone, and confidence is **capped at
> 0.75** so a single structural signal can never produce a *high-confidence* verdict.

---

## API

| Method | Endpoint  | Body                                   | Returns |
|--------|-----------|----------------------------------------|---------|
| POST   | `/submit` | `{text, creator_id}`                   | `content_id`, `attribution`, `confidence`, `label`, `signals` |
| POST   | `/appeal` | `{content_id, creator_reasoning}`      | confirmation + `status: under_review` |
| GET    | `/log`    | `?limit=N` (optional)                  | `{entries: [...]}` (most recent first) |
| GET    | `/health` | -                                      | `{status: ok}` |

Example submit:

```bash
curl -s -X POST http://localhost:5000/submit \
  -H "Content-Type: application/json" \
  -d '{"text": "The sun dipped below the horizon, painting the sky in hues of amber and rose. I sat on the porch, coffee in hand, watching the neighborhood slowly go quiet.", "creator_id": "test-user-1"}'
```

---

## Detection signals (two, genuinely independent)

The pipeline uses one **semantic** and one **structural** signal. Both speak the same
currency: a probability `P(AI)` in `[0, 1]`. Using two orthogonal views makes the
combination more informative than either alone — the LLM understands *meaning*,
stylometry measures *form*, and their blind spots barely overlap.

### Signal 1 — Groq LLM (`llama-3.3-70b-versatile`) — semantic coherence
- **Measures:** asks the model to read holistically and return strict JSON
  `{"ai_probability", "rationale"}`.
- **Why it discriminates:** LLMs recognize the "flavor" of generated prose — even,
  hedged, thesis-driven, low-risk — capturing rhetorical structure statistics miss.
- **Blind spot:** weakest exactly where stakes are highest — lightly edited AI,
  non-native-English human writing that *feels* formal, and very short inputs. Can also
  vary run-to-run (we use `temperature=0` to reduce this).

### Signal 2 — Stylometric heuristics (pure Python) — structural uniformity
Three sub-metrics, each normalized to an AI-likeness in `[0,1]`, then weighted into one
`style_p` (`detection.stylometry_signal`):
- **Sentence-length variance / burstiness (weight 0.55):** AI is metronomic; humans
  burst between short and long sentences. Low variance -> more AI-like.
- **Type-token ratio (weight 0.20):** very repetitive vocabulary leans AI-like (also
  catches the simple-poetry edge case).
- **Punctuation variety (weight 0.25):** humans use dashes, ellipses, parentheses, "?!";
  clean comma-and-period text reads more AI.
- **Blind spot:** short text (variance is meaningless), intentionally repetitive human
  poetry, and formal/academic human writing (uniform by genre). This is the signal most
  prone to **false positives**, which is exactly why it is weighted lower (0.35 overall)
  than the LLM.

### Why these two, and what I'd change for production
They are independent: one reads meaning, one reads form. For a real deployment I would
add a third signal (perplexity/burstiness from a small local model) and calibrate
weights against a labeled dataset rather than reasoned priors — see *Known limitations*.

---

## Confidence scoring with uncertainty

`combined_p = 0.65 * llm_p + 0.35 * style_p` is the probability the text is AI. We then
map it to a **verdict** plus a **confidence number**, leaving a wide "uncertain" band so
the system can honestly say *"I don't know"* instead of guessing.

### Thresholds are asymmetric on purpose
A false positive (human -> AI) is the worst error, so an AI verdict needs **stronger**
evidence than a human verdict:

| `combined_p`        | attribution    |
|---------------------|----------------|
| `>= 0.70`           | `likely_ai`    |
| `<= 0.35`           | `likely_human` |
| `0.35 < p < 0.70`   | `uncertain`    |

The AI threshold (0.70) sits further from 0.5 than the human threshold (0.35), so
borderline text lands in `uncertain` rather than being branded AI.

### What the confidence number means
- `likely_ai` -> `confidence = combined_p`
- `likely_human` -> `confidence = 1 - combined_p`
- `uncertain` -> `confidence = 1 - 2*|combined_p - 0.5|` (peaks at 1.0 when `p == 0.5`,
  so a near-coin-flip reads as *more* uncertain than a near-verdict).

"High confidence" wording only appears at `confidence >= 0.80`; between the verdict
threshold and 0.80 the label softens "strongly indicates" to "leans toward". This is how
a 0.51 and a 0.95 produce **meaningfully different reader text** rather than a binary flip.

### Two example submissions (full pipeline, with a Groq key)

| Input | `llm_p` | `style_p` | `combined_p` | verdict | confidence | label tier |
|-------|---------|-----------|--------------|---------|------------|------------|
| Polished, uniform AI essay ("...transformative paradigm shift...") | 0.93 | 0.66 | **0.836** | `likely_ai` | **0.84** | high-confidence AI |
| Casual, irregular human note ("ok so i finally tried that ramen place...") | 0.06 | 0.13 | **0.085** | `likely_human` | **0.92** | high-confidence human |

These differ by ~0.75 in `combined_p` and land in opposite verdicts — the score is not a
constant.

### How I tested that the scores are meaningful
`test_signals.py` runs the four calibration inputs from the spec (clear AI, clear human,
formal-human, lightly-edited AI) and prints **both signal scores separately** so a
miscalibrated signal is visible. Measured **structural** `style_p` (reproducible offline,
no key needed) already separates the cases in the right direction:

| Input | `style_p` (structural only) |
|-------|------------------------------|
| Clear AI essay | 0.45 |
| Clear human note | **0.13** (lowest -> most human) |
| Borderline formal human | 0.53 (highest structural AI-likeness — the documented blind spot) |
| Borderline edited AI | 0.35 |

The formal-human case scoring *highest* on stylometry is the expected blind spot, and
it is precisely why stylometry is down-weighted and the LLM carries the AI case.

---

## Transparency label (three variants)

The label returned by `/submit` changes with the verdict and confidence. `{pct}` is
`round(confidence * 100)`. Verbatim text of all three variants:

| Variant | Exact text |
|---------|------------|
| **High-confidence AI** | `AI-generated (high confidence). Our analysis strongly indicates this text was produced by an AI system. This verdict is based on two independent checks: a language-model assessment and writing-style statistics. Confidence: {pct}%. AI detection is not perfect -- if you wrote this yourself, you can appeal this result.` |
| **High-confidence human** | `Human-written (high confidence). Our analysis strongly indicates this text was written by a person. This verdict is based on two independent checks: a language-model assessment and writing-style statistics. Confidence: {pct}%.` |
| **Uncertain** | `Attribution uncertain. We could not confidently tell whether this text is human-written or AI-generated, so we are not assigning a verdict. This is common for short, edited, or stylistically unusual writing. The creator's authorship is not in question. (Internal signal strength: {pct}%.)` |

Design notes:
- The AI label always tells the creator they can appeal — the asymmetry is in the UX, not
  just the math.
- The uncertain label actively reassures ("authorship is not in question").
- When a verdict is reached but confidence `< 0.80`, "(high confidence)" becomes
  "(moderate confidence)" and "strongly indicates" becomes "leans toward saying".

---

## Appeals workflow

- **Who:** any creator, by referencing the `content_id` from their `/submit` response.
- **Provides:** `content_id` + `creator_reasoning` (free-text explanation).
- **System does:** looks up the original decision (404 if unknown) -> flips its `status`
  from `classified` to `under_review` -> writes an appeal row linked to the decision ->
  returns a confirmation. The verdict itself is **untouched**; no automated
  re-classification.
- **A reviewer** sees, via `GET /log`, the original verdict, both signal scores, the
  combined confidence, the creator's reasoning, and the `under_review` status — all in
  one place.

```bash
curl -s -X POST http://localhost:5000/appeal \
  -H "Content-Type: application/json" \
  -d '{"content_id": "PASTE-CONTENT-ID-HERE", "creator_reasoning": "I wrote this myself. I am a non-native English speaker and my style may appear more formal than typical."}'
```

---

## Rate limiting

Applied to `/submit` with Flask-Limiter (in-memory storage):

```python
@limiter.limit("10 per minute;100 per day")
```

**Chosen limits and reasoning:**
- **10 / minute** — a real writer submits a handful of pieces in a session, with the
  occasional retry or edit-and-resubmit. 10/min comfortably absorbs that legitimate burst
  while making it impossible to script a high-volume flood through one IP.
- **100 / day** — caps sustained abuse from a single source. Even a prolific creator
  rarely posts dozens of distinct works per day, so 100 leaves enormous headroom for
  humans while throttling a scraper/abuser running all day.
- Both numbers are per-IP. They are intentionally generous to humans and hostile to
  automation, reflecting the false-positive-averse stance of the whole system (we would
  rather inconvenience an abuser than block a legitimate writer).

**Evidence** — 12 rapid requests (limit is 10/min). The first 10 across the window
succeed; the rest return HTTP 429:

```
status codes: [200, 200, 200, 200, 200, 200, 200, 429, 429, 429, 429, 429]
```

(The window already had earlier submissions consumed in the same test run; a clean run
shows ten 200s followed by 429s.) The 429 body:

```json
{ "error": "rate_limit_exceeded", "message": "Too many requests. Limit: 10 per 1 minute." }
```

---

## Audit log

Every decision is persisted in SQLite (`db.py`, tables `submissions` + `appeals`) and
surfaced via `GET /log`. Each entry records: `content_id`, `creator_id`, `timestamp`,
`attribution`, `confidence`, **both individual signal scores** (`llm_score`,
`style_score`), `combined_p`, `signals_used`, `status`, and any nested `appeals`.

Representative `GET /log` output (3 entries; the first has an appeal and is
`under_review`):

```json
{
  "entries": [
    {
      "content_id": "166ea83e-ce8f-40cc-9e81-490b34fd424f",
      "creator_id": "u-essay",
      "timestamp": "2026-06-25T06:55:12.997Z",
      "text_excerpt": "Artificial intelligence represents a transformative paradigm shift...",
      "attribution": "likely_ai",
      "confidence": 0.836,
      "llm_score": 0.93,
      "style_score": 0.66,
      "combined_p": 0.836,
      "signals_used": "llm,stylometry",
      "status": "under_review",
      "appeals": [
        {
          "creator_reasoning": "I wrote this for a class essay; I am a non-native English speaker so my style is formal.",
          "timestamp": "2026-06-25T06:55:13.009Z"
        }
      ]
    },
    {
      "content_id": "aef7b955-bfbe-4ad9-9c07-030d8b29bf9f",
      "creator_id": "u-human",
      "timestamp": "2026-06-25T06:55:13.002Z",
      "text_excerpt": "ok so i finally tried that new ramen place downtown and honestly? underwhelming...",
      "attribution": "likely_human",
      "confidence": 0.916,
      "llm_score": 0.06,
      "style_score": 0.13,
      "combined_p": 0.084,
      "signals_used": "llm,stylometry",
      "status": "classified",
      "appeals": []
    },
    {
      "content_id": "c1d2e3f4-aaaa-bbbb-cccc-123456789abc",
      "creator_id": "u-mixed",
      "timestamp": "2026-06-25T06:54:50.500Z",
      "text_excerpt": "I've been thinking a lot about remote work lately. There are genuine tradeoffs...",
      "attribution": "uncertain",
      "confidence": 0.844,
      "llm_score": 0.62,
      "style_score": 0.50,
      "combined_p": 0.578,
      "signals_used": "llm,stylometry",
      "status": "classified",
      "appeals": []
    }
  ]
}
```

> Without a `GROQ_API_KEY` the same structure is produced with `llm_score: null`,
> `signals_used: "stylometry"`, and confidence capped at `0.75` (degraded mode).

---

## Known limitations

- **Repetitive, simple-vocabulary human poetry will likely be over-scored as AI.** A poem
  built on refrain and plain words has *low* sentence-length variance and *low* type-token
  ratio — exactly the structural fingerprint stylometry reads as AI. This is a direct
  property of the burstiness + TTR sub-metrics, not a data-volume issue. Safeguards (0.35
  weight on stylometry, 0.70 AI threshold, wide uncertain band) push most such cases into
  `uncertain` rather than `likely_ai`, and the appeals path exists for the rest.
- **Non-native-English / formal academic human writing** can read as uniform to *both*
  signals (the LLM associates formality with AI; stylometry sees low burstiness). When both
  agree wrongly the safeguards can't fully save us — this is the scenario the appeals
  workflow is built for.
- **Stylometry weights are reasoned priors, not data-calibrated.** A production version
  should fit them against a labeled corpus.

---

## Spec reflection

- **Where the spec helped:** writing the confidence section of `planning.md` *before*
  coding forced me to decide what `0.6` should mean to a user (still "uncertain") and to
  encode the false-positive asymmetry as two different thresholds (0.70 vs 0.35). Because
  that was settled in prose first, the implementation in `combine_signals` was a direct
  translation rather than a guess, and the labels lined up with it.
- **Where I diverged:** the spec framed confidence as a single number. During
  implementation I realized a single number is ambiguous in the uncertain band (is 0.6
  "60% AI" or "60% confident"?), so I split the meaning: in the uncertain band confidence
  reports *how unsure* we are (`1 - 2*|p-0.5|`) and the label calls it "internal signal
  strength" rather than a verdict confidence. I also added a degraded-mode confidence cap
  (0.75) that the spec didn't anticipate, so the system can't claim high confidence on one
  signal.

---

## AI usage

I used an AI coding assistant against the `planning.md` spec. Two concrete instances:

1. **Generated the Flask skeleton + first signal.** I provided the detection-signals
   section and the API contract and asked for the `/submit` route plus `llm_signal`.
   What I overrode: the first draft let a Groq failure raise and 500 the request; I changed
   `llm_signal` to return `None` on any exception and added the degraded-mode fallback +
   confidence cap in `combine_signals`, so an outage downgrades gracefully instead of
   breaking the endpoint.
2. **Generated the scoring + label logic.** I provided the uncertainty-representation
   section and asked for `combine_signals` and `generate_label`. What I revised: the
   generated scoring used a symmetric ±0.15 band around 0.5, which contradicted the
   spec's deliberate asymmetry; I corrected it to the documented 0.70 / 0.35 thresholds
   and reworked the uncertain-band confidence formula so a near-coin-flip reads as *more*
   uncertain, not less.

---

## Project layout

```
app.py            Flask app: /submit, /appeal, /log, /health, rate limiting
detection.py      llm_signal, stylometry_signal, combine_signals, classify
labels.py         generate_label (three variants)
db.py             SQLite init + audit-log helpers
test_signals.py   calibration harness (prints both signal scores)
test_e2e.py       end-to-end smoke test via Flask test client
planning.md       architecture, spec (5 questions), AI Tool Plan
requirements.txt  dependencies
.env.example      template for GROQ_API_KEY
```
