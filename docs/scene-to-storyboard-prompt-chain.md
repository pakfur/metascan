# Seed → Cinematic Scene → Storyboard Panels
### A four-stage prompt chain for a local ~30B model

Target: 2–3 sentence seed in, structured shot list out, ready to feed a diffusion prompt renderer.

---

## 0. Why four stages

A 30B-class model asked to "write a cinematic scene breakdown" in one shot will produce fluent
prose, invent camera moves that don't serve anything, drift on character description by panel 8,
and quietly ignore your shot count. Each of those failures has a different fix, so each gets its
own stage with its own decoding settings.

| Stage | Job | Output | Temp | Thinking |
|---|---|---|---|---|
| 0 — Bible | Extract/invent the fixed facts | JSON, once | 0.8 | on |
| 1 — Beats | Dramatic spine, value turns | JSON, once | 0.7 | on |
| 2 — Decoupage | Beats → shots w/ camera grammar | JSONL, per beat | 0.35 | **off** |
| 3 — Panel render | Shot → image prompt | text, per panel | 0.5 | **off** |

Qwen3 hybrid thinking: leave it on for 0 and 1 where you want reasoning about structure. Turn it
off (`/no_think` or `enable_thinking=false`) for 2 and 3 — reasoning traces are the single biggest
source of JSON corruption in structured output stages.

**Re-inject the bible verbatim into every Stage 2 and Stage 3 call.** Do not rely on the model
remembering what the character looks like from 6000 tokens ago. This is a string concat in your
harness, not a context-window bet.

---

## Shot budget math

Panels are not seconds. Panels are *shots*, plus an extra panel for any shot with significant
camera or subject movement (start frame + end frame).

```
target_seconds = 150                    # midpoint of 2–3 min
asl = pacing_table[pacing]              # average shot length
shot_count = round(target_seconds / asl)
panel_count = shot_count + moving_shot_count
```

| Pacing | ASL | Shots @150s | Use for |
|---|---|---|---|
| `contemplative` | 8s | ~19 | dread, grief, landscape, lyrical |
| `standard` | 5s | ~30 | dialogue, most drama |
| `propulsive` | 3s | ~50 | chase, argument, panic |
| `frenetic` | 1.5s | ~100 | combat, montage — *storyboard as key frames only* |

For `frenetic`, don't board every shot. Board the 20–25 key frames and mark the rest as
`"coverage_burst"` with a count. Nobody boards 100 panels for a 2-minute scene, and the model
will produce mush if you ask.

**Practical default: 24–32 panels.** Pass the number in explicitly. Small models treat "about
30 shots" as a suggestion and "produce exactly 7 shots for this beat, numbered 1 through 7" as
an instruction.

---

## Controlled vocabularies

This is the part that actually makes the output usable. Free-text camera fields produce
"the camera moves in a cinematic way." Enums produce shot lists. Put these in the system prompt
and enforce them with constrained decoding.

```yaml
shot_size:     [EWS, WS, MLS, MS, MCU, CU, ECU, INSERT, TWO_SHOT, OTS]
angle:         [EYE, LOW, HIGH, OVERHEAD, DUTCH, WORMS, POV]
movement:      [STATIC, PAN_L, PAN_R, TILT_UP, TILT_DOWN, PUSH_IN, PULL_OUT,
                TRUCK_L, TRUCK_R, CRANE_UP, CRANE_DOWN, HANDHELD, STEADICAM,
                WHIP_PAN, RACK_FOCUS, ARC_L, ARC_R]
lens_mm:       [14, 24, 35, 50, 85, 135]
composition:   [THIRDS_L, THIRDS_R, CENTERED, SYMMETRICAL, NEGATIVE_SPACE,
                FRAME_IN_FRAME, LEADING_LINES, DEEP_STAGING, FLAT_STAGING]
light_key:     [FRONT, SIDE_L, SIDE_R, BACK, TOP, UNDER, AMBIENT]
light_quality: [HARD, SOFT, DAPPLED, PRACTICAL, MOTIVATED_WINDOW, FIRELIGHT]
transition_in: [HARD_CUT, MATCH_CUT, DISSOLVE, SMASH_CUT, L_CUT, J_CUT, WIPE, FADE]
screen_dir:    [LOOK_L, LOOK_R, TO_CAMERA, AWAY, NEUTRAL]
```

### Grammar rules to state as constraints

These are what separate a shot list from a list of pictures:

1. **The axis.** The bible declares an `action_axis`. Every character is assigned a
   `screen_side` (LEFT/RIGHT). All shots keep them on that side unless a shot is explicitly
   flagged `crosses_line: true` with a stated reason (a move on camera, a neutral shot, or a
   deliberate disorientation). Validate this in code — it is the #1 thing AI-generated boards
   get wrong.
2. **Eyeline reciprocity.** If character A is `LOOK_R`, their reverse on B must be `LOOK_L`.
3. **No more than two consecutive shots at the same `shot_size`.** Forces the model to build
   coverage instead of 30 medium shots.
4. **Earn the close-up.** The tightest `shot_size` in the scene is reserved for the beat with
   the largest `value_shift`. Push in on the turn, not on exposition.
5. **Motivate every move.** A `movement` other than `STATIC` requires a non-empty
   `movement_motivation` referencing subject action or emotional pressure. This kills the
   gratuitous drone-swoop reflex.
6. **Establish before you fragment.** First shot of a new space is `WS` or `EWS` unless the
   scene is deliberately withholding geography.
7. **Two panels per moving shot.** `panel_a` = start frame, `panel_b` = end frame.

---

## Stage 0 — Scene Bible

```
SYSTEM:
You are a film development executive building a scene bible. You extract the fixed,
unchanging facts of a scene so that downstream artists stay consistent.

You do not write prose. You output one JSON object and nothing else.

Rules:
- Invent only what is necessary. If the seed doesn't specify it, choose the option that
  creates the most dramatic pressure, then commit to it permanently.
- Character descriptions must be renderable: age, build, hair, face, wardrobe, distinguishing
  feature. No interiority, no backstory, no adjectives a camera cannot see.
- style_lock is a comma-separated phrase list that will be appended to EVERY image prompt.
  Keep it to 8-14 tokens covering: film stock/era, color palette, lighting philosophy,
  lens character, grain/texture.
- action_axis is the imaginary line of the scene. Assign each character a permanent
  screen_side relative to it.

Output schema:
{
  "logline": "one sentence, present tense",
  "dramatic_question": "the yes/no question the scene answers",
  "genre_register": "",
  "pacing": "contemplative|standard|propulsive|frenetic",
  "target_seconds": 150,
  "shot_budget": 30,
  "characters": [
    {"id":"", "name":"", "screen_side":"LEFT|RIGHT",
     "physical":"", "wardrobe":"", "signature_prop":"",
     "want":"", "obstacle":""}
  ],
  "location": {
    "name":"", "geography":"spatial layout in 2-3 clauses",
    "time_of_day":"", "weather":"", "key_light_source":"",
    "textures":"", "sound_bed":""
  },
  "action_axis": "describe the line, e.g. 'the kitchen table between them'",
  "style_lock": "",
  "palette": ["#hex or named", "...", "..."],
  "aspect_ratio": "2.39:1|1.85:1|16:9|4:3"
}

USER:
SEED: {{seed}}
PACING: {{pacing}}
TARGET_SECONDS: {{target_seconds}}
```

---

## Stage 1 — Beat Sheet

```
SYSTEM:
You are a screenwriter breaking a scene into dramatic beats.

A beat is a unit of action/reaction where the emotional value of the scene CHANGES.
If the value does not change, it is not a beat — it is filler. Delete it.

Requirements:
- 5 to 8 beats. No more.
- Each beat carries a value charge from -5 to +5 measured from the protagonist's POV.
- charge_out of beat N must equal charge_in of beat N+1.
- The scene must TURN: at least one beat where |charge_out - charge_in| >= 4.
  This is the pivot. Mark it is_turn: true. Exactly one beat is the turn.
- The scene must not end on the same charge polarity it started on.
- subtext is what the character means but does not say. It must differ from the dialogue
  or action. If they are the same, rewrite.
- Allocate seconds across beats summing to target_seconds. The turn gets the most room
  or the least — never the average.

You output one JSON object and nothing else.

{
  "beats": [
    {"n":1, "name":"", "seconds":0,
     "action":"what physically happens, camera-visible only",
     "charge_in":0, "charge_out":0, "is_turn":false,
     "subtext":"", "sound_cue":"", "tension_source":""}
  ],
  "turn_rationale": "",
  "closing_image": "the last thing we see, and why it lands"
}

SCENE BIBLE:
{{bible_json}}
```

Validate in code: charge continuity, exactly one `is_turn`, seconds sum, polarity flip.
Retry the stage on failure rather than trying to fix it downstream.

---

## Stage 2 — Decoupage (run once per beat)

```
SYSTEM:
You are a director doing decoupage: converting one dramatic beat into a shot list.

You output JSON Lines — one JSON object per line, one line per shot, nothing else.
No array brackets. No commentary. No markdown fences.

You must produce EXACTLY {{n_shots}} shots for this beat, numbered {{start}} to {{end}}.

CAMERA GRAMMAR — you may only use these values:
{{enums}}

HARD RULES:
1. Characters stay on their assigned screen_side. Setting crosses_line true requires a
   stated reason in movement_motivation.
2. If a character looks LOOK_R, their reverse partner looks LOOK_L.
3. Never use the same shot_size more than twice in a row.
4. Reserve the tightest shot_size for the beat marked is_turn.
5. Any movement other than STATIC requires movement_motivation naming what in the
   subject's behavior or emotional state pulls the camera. "For drama" is not a reason.
6. Every shot states what it REVEALS that the previous shot did not. If it reveals
   nothing, it is a duplicate — change it.

Per-shot schema:
{"shot":0,"beat":0,"seconds":0.0,
 "shot_size":"","angle":"","movement":"","movement_motivation":"",
 "lens_mm":50,"depth_of_field":"shallow|medium|deep",
 "composition":"","subject":"","subject_action":"",
 "screen_dir":"","crosses_line":false,
 "light_key":"","light_quality":"","light_note":"",
 "foreground":"","background":"",
 "reveals":"","emotional_intent":"",
 "transition_in":"",
 "panel_a":"description of the frame at shot start",
 "panel_b":"description at shot end, or null if STATIC"}

SCENE BIBLE:
{{bible_json}}

PRECEDING SHOT (maintain continuity from this):
{{last_shot_json_or_none}}

BEAT TO BREAK DOWN:
{{beat_json}}
```

Per-beat calls with the previous shot passed in keeps continuity without needing the model to
hold 30 shots in working memory. JSONL means a malformed line costs you one shot, not the
whole list.

---

## Stage 3 — Panel Prompt Render

Feed each `panel_a` / `panel_b` here. This is the stage that hands off to your existing
per-model prompt tooling.

```
SYSTEM:
You convert a storyboard panel specification into a single image generation prompt.

Rules:
- Output ONE line of prompt text. No preamble, no quotes, no explanation.
- Order: [shot size + angle] [subject + action] [wardrobe] [foreground] [background]
  [lighting] [lens + DOF] [style_lock]
- Copy character physical and wardrobe descriptions VERBATIM from the bible. Do not
  paraphrase, do not abbreviate, do not "improve" them. Consistency beats elegance.
- Append style_lock verbatim as the final clause.
- Translate camera enums to natural photographic language:
  MCU -> "medium close-up, chest up"; LOW -> "low angle looking up";
  85mm shallow -> "85mm lens, shallow depth of field, background falloff"
- Do not describe emotion as a label ("she is sad"). Describe the visible physical
  evidence of it ("jaw set, eyes fixed on the floor, shoulders dropped").
- Do not include camera movement. A still frame cannot show a dolly.

SCENE BIBLE:
{{bible_json}}

PANEL:
{{panel_json}}
```

The "no emotion labels, only physical evidence" rule matters more than it looks — diffusion
models render "sad woman" as a stock expression, and render "jaw set, eyes fixed on the floor"
as an actual performance.

---

## Harness notes for local inference

**Constrained decoding is not optional at this size.** Stages 0–2 should run under a grammar:

- llama.cpp: convert the schemas to GBNF (`json_schema_to_grammar.py` ships with it), pass
  `--grammar-file`
- vLLM: `guided_json=schema` via xgrammar
- Ollama: `format` parameter accepts a JSON schema directly

Without it you will spend more code on repairing output than on the pipeline.

**Context:** Stage 2 calls run ~2–4k tokens each. You never need a long context window, which
means you can run the model at full precision on a single card instead of trading quality for
a 32k window you don't use.

**Sampling for Stage 2:** temp 0.35, top_p 0.9, and a mild `repetition_penalty` (1.05). Higher
temp here produces variety in camera choices, which sounds good and isn't — you want the
*coverage logic* to be boring and correct, and the variety to come from Stage 1's beats.

**Validators worth writing** (all cheap, all catch real failures):

```python
assert_shot_count(shots, bible["shot_budget"])
assert_no_triple_repeat(shots, key="shot_size")
assert_line_discipline(shots, bible["characters"])      # screen_side violations
assert_eyeline_reciprocity(shots)
assert_tightest_on_turn(shots, beats)
assert_all_moves_motivated(shots)
assert_seconds_sum(shots, bible["target_seconds"], tol=0.15)
```

Failures should trigger a targeted re-roll of the offending beat with the violation stated in
the user turn ("Shot 14 places KESTREL on the left; she is assigned RIGHT. Regenerate beat 4.")
Small models correct well against specific complaints and badly against "try again."

---

## Worked micro-example

**Seed:** *A woman returns to her childhood home to find her brother already there, packing
their late mother's things. Neither of them expected the other. Neither says hello.*

**Stage 1 output, abbreviated:**

| n | beat | sec | in→out | turn | subtext |
|---|---|---|---|---|---|
| 1 | She enters, hears movement upstairs | 22 | 0 → -1 | | *I thought I'd have this alone* |
| 2 | Recognition on the landing | 18 | -1 → -2 | | *You didn't call me* |
| 3 | He keeps packing, doesn't stop | 30 | -2 → -3 | | *If I stop I'll have to feel it* |
| 4 | She picks up a box he'd sealed | 25 | -3 → -4 | | *You don't get to decide what's kept* |
| 5 | He takes it back. It tears. | 20 | -4 → **+2** | ✓ | *I can't do this either* |
| 6 | They sit on the floor with the spill | 35 | +2 → +1 | | *neither says it* |

**Stage 2, beat 5, three of seven shots:**

```jsonl
{"shot":22,"beat":5,"seconds":2.5,"shot_size":"INSERT","angle":"HIGH","movement":"STATIC","lens_mm":85,"depth_of_field":"shallow","composition":"CENTERED","subject":"cardboard box seam","subject_action":"two hands gripping opposite flaps, tendons raised","screen_dir":"NEUTRAL","crosses_line":false,"light_key":"SIDE_L","light_quality":"MOTIVATED_WINDOW","reveals":"the physical stalemate, without faces","emotional_intent":"stalemate made literal","transition_in":"HARD_CUT","panel_a":"...","panel_b":null}
{"shot":23,"beat":5,"seconds":1.0,"shot_size":"ECU","angle":"EYE","movement":"STATIC","lens_mm":135,"depth_of_field":"shallow","composition":"THIRDS_L","subject":"MAREN","subject_action":"eyes drop to the tearing seam, breath stops","screen_dir":"LOOK_R","crosses_line":false,"light_key":"SIDE_L","light_quality":"MOTIVATED_WINDOW","reveals":"she registers what she's doing","emotional_intent":"the turn lands here","transition_in":"SMASH_CUT","panel_a":"...","panel_b":null}
{"shot":24,"beat":5,"seconds":4.5,"shot_size":"MS","angle":"EYE","movement":"PULL_OUT","movement_motivation":"both let go simultaneously; camera retreats as the fight leaves them","lens_mm":35,"depth_of_field":"medium","composition":"NEGATIVE_SPACE","subject":"MAREN and DECLAN","subject_action":"hands open, box drops, contents fan across floorboards","screen_dir":"NEUTRAL","crosses_line":false,"light_key":"SIDE_L","light_quality":"SOFT","reveals":"the room again — how much is still unpacked","emotional_intent":"release, and the size of what's left","transition_in":"HARD_CUT","panel_a":"medium two-shot, hands still on box","panel_b":"wider, both figures small against stacked boxes, contents spilled between them"}
```

Note the ECU at shot 23 — it's the tightest frame in the scene and it sits on the turn. That's
rule 4 doing its job, and it's the difference between a shot list and a mood board.
