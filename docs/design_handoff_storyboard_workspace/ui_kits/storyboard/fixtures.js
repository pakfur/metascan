window.SB = (function () {
  const BASE = window.SB_ASSET_BASE || '../..'
  const F = (n) => BASE + '/assets/placeholders/frame-' + String(n).padStart(2, '0') + '.png'
  const subjects = [
    { id: 1, name: 'Mara', description: 'Late twenties, canvas jacket, cropped hair.', lora_name: 'mara_v3.safetensors' },
    { id: 2, name: 'Ines', description: 'Older, silver rings, always mid-sentence.', lora_name: null },
    { id: 3, name: 'The clerk', description: 'Bored, apron, chipped nail polish.', lora_name: null },
  ]
  const beat = (id, o) => Object.assign({
    id, duration_s: 2.5, action: '', shot_size: 'MCU', angle: 'eye', lens: 'normal',
    subject_ids: [1], camera_motion: null, camera_amplitude: 'small', camera_speed: 'slow',
    is_cut: 0, dialog: [], sound: null, prompt: null, prompt_locked: 0, prompt_source: 'llm',
    selected_image_id: null, images: [],
  }, o)
  const scenes = [
    {
      id: 1, name: 'Kitchen, dawn', subtitle: 'interior · dawn',
      setting: 'Cold light through slatted blinds, unwashed cups on the counter.',
      location: 'interior', time_of_day: 'dawn', mood: 'held breath', lighting: 'hard side light',
      panels: [
        {
          id: 11, sort_order: 0, action: 'Mara waits for the kettle and does not look at the door.',
          duration_s: 6.0, image_loras: [{ name: 'mara_v3.safetensors', strength: 0.85 }, { name: 'filmgrain_xl.safetensors', strength: 0.4 }, { name: 'dmd2_sdxl_4step_lora_fp16.safetensors', strength: 1.0 }], video_loras: [{ name: 'h3_motion_smooth.safetensors', strength: 0.6 }, { name: 'filmgrain_xl.safetensors', strength: 0.25 }],
          video_prompt: '[Shot 1] MCU, eye level, normal lens. Mara stands at the counter, hands flat on the worktop.\n[Camera] push in, small, slow.\n[Lighting] hard side light through slatted blinds.\n[Sound] kettle rising, no music.\n[Dialogue] none.\n[Negative] no crowd, no text overlay.',
          video_prompt_source: 'compiled', video_prompt_locked: 0, video_prompt_warnings: [], video_anchor: 'keeper', video_compiled_anchor: 'keeper',
          videos: [{ id: 91, file_path: F(12), seed: 9931, variant_index: 0 }, { id: 92, file_path: F(13), seed: 9932, variant_index: 1 }],
          beats: [
            beat(101, { action: 'She sets the cup down without looking up.', duration_s: 2.5, camera_motion: 'push_in', images: [
              { id: 1001, file_path: F(7), seed: 481314106, variant_index: 0 },
              { id: 1002, file_path: F(1), seed: 481314107, variant_index: 1 },
              { id: 1003, file_path: F(2), seed: 481314108, variant_index: 2 },
              { id: 1004, file_path: F(3), seed: 481314109, variant_index: 3 },
            ], selected_image_id: 1001, prompt: 'medium close-up, eye level, normal lens, Mara at a kitchen counter at dawn, hands flat on the worktop, hard side light through slatted blinds, unwashed cups, muted palette, 35mm film grain', subject_ids: [1] }),
            beat(102, { action: 'The bell over the door does not ring.', duration_s: 3.0, camera_motion: 'pan_right', is_cut: 1, subject_ids: [1, 2],
              dialog: [{ subject_id: 2, voice: null, delivery: 'flat', language: 'English', text: 'You left it unlocked again.' }],
              sound: 'kettle at full boil, a chair scraping off screen',
              images: [{ id: 1005, file_path: F(8), seed: 55120, variant_index: 0 }], selected_image_id: 1005,
              prompt: 'medium shot, eye level, normal lens, Mara and Ines in a dawn kitchen, doorway empty behind them, cold slatted light', shot_size: 'MS' }),
            beat(103, { action: 'Cut to the empty doorway.', duration_s: 0.5, shot_size: 'CU', subject_ids: [], images: [], prompt: null, prompt_source: null }),
          ],
        },
        {
          id: 12, sort_order: 1, action: 'Ines answers from the hallway.', duration_s: 4.0,
          image_loras: [], video_loras: [], video_prompt: null, video_prompt_source: null,
          video_prompt_locked: 0, video_prompt_warnings: ['no dialogue block: H3 will improvise'], video_anchor: 'prev_last', video_compiled_anchor: null, videos: [],
          beats: [beat(104, { action: 'Ines leans into frame, still talking.', subject_ids: [2], shot_size: 'MCU', camera_motion: 'static', images: [{ id: 1006, file_path: F(9), seed: 771, variant_index: 0 }], selected_image_id: 1006, prompt: 'medium close-up of Ines mid-sentence in a narrow hallway, warm bulb overhead' })],
        },
        {
          id: 13, sort_order: 2, action: 'The kettle boils over.', duration_s: 2.0,
          image_loras: [], video_loras: [], video_prompt: null, video_prompt_source: null,
          video_prompt_locked: 0, video_prompt_warnings: [], video_anchor: null, video_compiled_anchor: null, videos: [],
          beats: [beat(105, { action: 'Steam floods the window.', subject_ids: [], shot_size: 'ECU', images: [] })],
        },
        {
          id: 14, sort_order: 3, action: 'Mara takes the cup to the table.', duration_s: 3.0,
          image_loras: [], video_loras: [], video_prompt: null, video_prompt_source: null,
          video_prompt_locked: 1, video_prompt_warnings: [], video_anchor: null, video_compiled_anchor: null, videos: [],
          beats: [beat(106, { action: 'She sits without pulling the chair in.', images: [{ id: 1007, file_path: F(10), seed: 3311, variant_index: 0 }], selected_image_id: null, prompt_locked: 1, prompt_source: 'user', prompt: 'medium close-up, Mara seated at a kitchen table, chair left pulled out, cold dawn light' })],
        },
      ],
    },
    {
      id: 2, name: 'Courtyard', subtitle: 'exterior · midday',
      setting: 'Flat white light, one plastic chair, the gate standing open.',
      location: 'exterior', time_of_day: 'midday', mood: 'exposed', lighting: 'flat overcast',
      panels: [
        { id: 21, sort_order: 0, action: 'She crosses the courtyard.', duration_s: 5.0, image_loras: [], video_loras: [], video_prompt: null, video_prompt_source: null, video_prompt_locked: 0, video_prompt_warnings: [], video_anchor: null, video_compiled_anchor: null, videos: [],
          beats: [beat(201, { action: 'Mara crosses left to right, fast.', shot_size: 'WS', camera_motion: 'truck_right', camera_speed: 'fast', images: [{ id: 2001, file_path: F(4), seed: 61, variant_index: 0 }], selected_image_id: 2001, prompt: 'wide shot, flat overcast courtyard, Mara crossing frame left to right' })] },
        { id: 22, sort_order: 1, action: 'The clerk watches from the gate.', duration_s: 3.0, image_loras: [], video_loras: [], video_prompt: null, video_prompt_source: null, video_prompt_locked: 0, video_prompt_warnings: [], video_anchor: null, video_compiled_anchor: null, videos: [],
          beats: [beat(202, { action: 'He does not move out of the way.', subject_ids: [3], shot_size: 'MLS', images: [], prompt: null, prompt_source: null })] },
      ],
    },
    {
      id: 3, name: 'Stairwell', subtitle: 'interior · night',
      setting: null, location: 'interior', time_of_day: 'night', mood: null, lighting: null,
      panels: [
        { id: 31, sort_order: 0, action: 'Two floors of nothing happening.', duration_s: 4.0, image_loras: [], video_loras: [], video_prompt: null, video_prompt_source: null, video_prompt_locked: 0, video_prompt_warnings: [], video_anchor: null, video_compiled_anchor: null, videos: [],
          beats: [beat(301, { action: 'Mara climbs into the dark.', shot_size: 'MLS', camera_motion: 'tracking', images: [], prompt: null, prompt_source: null })] },
      ],
    },
  ]
  const list = [
    { id: 1, name: 'Coffee shop meet-cute', aspect_ratio: '16:9', target_model: 'flux1', updated_at: '8/23/2025, 2:15:02 PM' },
    { id: 2, name: 'Cold open — stairwell', aspect_ratio: '2.39:1', target_model: 'qwen', updated_at: '8/21/2025, 9:04:47 AM' },
    { id: 3, name: 'Product loop (vertical)', aspect_ratio: '9:16', target_model: 'zimage', updated_at: '8/14/2025, 6:38:11 PM' },
  ]
  const tree = {
    id: 1, name: 'Coffee shop meet-cute', aspect_ratio: '16:9', target_model: 'flux1',
    batch_size: 4, base_seed: 481314106, video_target: 'minimax', video_mode: 'ref2va',
    video_preset_id: 4, preset_id: 2, subjects, scenes,
    style_block: 'muted palette, 35mm film grain, no text',
    negative: 'text overlay, watermark, extra fingers',
    source_text: 'Two women who have known each other too long share a kitchen at dawn. Neither says the thing.',
    outline: '{\n  "logline": "Two women share a kitchen at dawn and neither says the thing.",\n  "scenes": [\n    { "name": "Kitchen, dawn", "beats": 3 },\n    { "name": "Courtyard", "beats": 2 },\n    { "name": "Stairwell", "beats": 1 }\n  ]\n}',
  }
  const loraOptions = ['mara_v3.safetensors', 'filmgrain_xl.safetensors', 'dmd2_sdxl_4step_lora_fp16.safetensors', 'sts_age_slider_v1.safetensors']
  return { F, tree, list, subjects, loraOptions,
    SHOT_SIZES: ['ECU', 'CU', 'MCU', 'MS', 'MLS', 'WS', 'EWS'],
    ANGLES: ['eye', 'low', 'high', 'overhead', 'dutch', 'ots', 'pov'],
    LENSES: ['wide', 'normal', 'tele', 'macro'],
    CAMERA_MOTIONS: ['zoom_in', 'zoom_out', 'push_in', 'pull_out', 'pan_left', 'pan_right', 'truck_left', 'truck_right', 'tilt_up', 'tilt_down', 'pedestal_up', 'pedestal_down', 'arc', 'tracking', 'static', 'shake_slight', 'shake_strong', 'pov', 'roll_cw', 'roll_ccw'],
    CAMERA_AMPLITUDES: ['small', 'large'],
    CAMERA_SPEEDS: ['slow', 'fast'],
    ASPECT_RATIOS: ['1:1', '4:3', '16:9', '2.39:1', '9:16'],
    TARGET_MODELS: ['sd', 'pony', 'flux1', 'flux2', 'zimage', 'chroma', 'qwen'],
    VIDEO_MODES: ['t2va', 'i2va', 'fl2va', 'ref2va'],
    COMPOSE_STAGES: ['outline', 'scenes', 'shots', 'beats'],
  }
})()
