[← Back to README](../README.md)

# Image-to-Video cadence templates

A cadence template fixes the shot structure of an Image-to-Video clip —
timing, shot size, angle, camera motion, cuts, who is on screen and who
speaks. The VLM fills in only the prose. Metascan then assembles the
MiniMax H3 prompt and lints it against the template.

Templates are JSON files under `data/i2v_templates/`, one per file, loaded
at first use and validated against the same camera vocabulary the
storyboard uses. A bad file fails loudly, naming the file and field.

## Beats and shots

A template lists **beats**. H3 **shots** are derived from them:

| `transition` | Effect |
|---|---|
| `continuous` | Stays in the current shot; rendered as `At MM:SS.mmm, the camera …`. |
| `cut` | Opens a new `[Shot n] At MM:SS.mmm, the shot cuts to …`. |
| `j_cut` | Opens a new shot, but the speaker's line is rendered first, "carrying over from the previous shot" (audio leads picture). |

The first beat is always `continuous`: it is anchored to the source image.
Use a cut when the shot introduces new information (subject, space,
viewpoint); use camera motion when only the distance changes — that is
the MiniMax guide's own rule.

## Fields

| Field | Type | Rule |
|---|---|---|
| `id` | string | Must equal the file stem. Unique. |
| `name`, `description` | string | Shown in the picker. |
| `duration_s` | number > 0 | The clip length this template sets. Must be one of the configured `i2v.durations`, or the template is loaded but reported unavailable with the reason (so a config change never breaks the library). |
| `look` | string, optional | Rendered once, as the opener of Shot 1, in the guide's "Live-action, cinematic, …" position. |
| `soundscape_hint` | string, optional | Guidance to the VLM for `overall_soundscape`. The VLM still writes the field. |
| `roles[]` | list, may be empty | `id` (string, unique within the template), `note` (string). |
| `beats[]` | list, ≥ 1 | See below. |

Per beat:

| Field | Rule |
|---|---|
| `start_s`, `end_s` | Beats must tile `[0, duration_s]` exactly: first `start_s` is 0, each `start_s` equals the previous `end_s`, last `end_s` equals `duration_s`. Every beat ≥ 0.5 s. |
| `transition` | `continuous` \| `cut` \| `j_cut`. The first beat must be `continuous`. |
| `cast` | list of role ids from `roles`. May be empty (a detail insert). |
| `speaker` | a role id in `cast`, or `null`. |
| `camera.shot_size` | `SHOT_SIZE_VALUES` (`ECU CU MCU MS MLS WS EWS`). Required. |
| `camera.angle` | `ANGLE_VALUES` minus `pov` (`eye low high overhead dutch ots`). Required. |
| `camera.camera_motion` | `CAMERA_MOTION_VALUES` minus `pov`. Required. |
| `camera.camera_amplitude` | `small` \| `large`, optional. |
| `camera.camera_speed` | `slow` \| `fast`, optional. |
| `camera.lens` | `LENS_VALUES`, optional. Rendered as a phrase ("a macro detail shot") when present. |
| `note` | string, optional. Given to the VLM as the beat's brief. |

Vocabularies are imported from `storyboard_parse.py` /
`storyboard_story.py`, never redeclared. A template written in another
vocabulary (`"EYE"`, `"STATIC"`, `"OTS"` as a shot size) raises
`I2vTemplateError` naming the file and field at load. `pov` is excluded
because i2v has no POV mode.

## Roles and the source picture

A template declares roles (`A`, `B`). Before writing, the VLM binds each
to a person it sees in the source image and describes them once. If the
image shows fewer people than the roles need, the VLM invents the rest;
an invented person is kept out of the first shot (the only shot the
picture anchors) and enters at their next beat. The prompt's warnings say
when this happened.

## Duration

A template fixes the clip length. Choosing it in the dialog sets Duration
and locks the selector. A template whose `duration_s` is not one of your
configured `i2v.durations` is listed but unavailable.

## Worked examples

`dialog_ots_15.json`, `melee_12.json` and `intimate_15.json` ship with
metascan and cover the three transitions. Copy one to start a new
template; the `id` must equal the file name.
