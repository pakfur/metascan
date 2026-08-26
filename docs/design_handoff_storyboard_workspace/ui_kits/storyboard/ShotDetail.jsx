const { Button, IconButton, Field, TextInput, LoraListEditor, BeatPill, VideoTakeTile, ConfirmBanner, JobBadge } = window.MetascanDesignSystem_f2fbbd

function ShotDetail({ panel, subjects, selectedBeatId, onSelectBeat, onPatchPanel, onMoveBeat, onAddBeat, jobFor }) {
  const [rebeatConfirm, setRebeatConfirm] = React.useState(false)
  const total = panel.beats.reduce((s, b) => s + b.duration_s, 0)
  const over = total > 15
  return (
    <div style={{ flexShrink: 0, maxHeight: '44vh', overflowY: 'auto', borderTop: '1px solid var(--surface-border)', background: 'var(--surface-card)', padding: '12px 20px 16px', display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
        <h4 style={{ margin: 0, fontSize: 14, color: 'var(--text-color)', fontWeight: 600 }}>Panel {(panel.sort_order ?? 0) + 1}</h4>
        <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
          <Button size="sm" disabled={panel.beats.length === 0}>Reroll shot</Button>
          <Button size="sm" disabled={panel.beats.length === 0}>Re-synth shot</Button>
        </div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        <Field label="Action">
          <TextInput style={{ maxWidth: 320 }} value={panel.action} onChange={(e) => onPatchPanel({ action: e.target.value })} />
        </Field>
        <Field label="Duration (s)">
          <TextInput readOnly style={{ maxWidth: 120 }} value={total.toFixed(1)}
            title="Derived from the sum of this shot's beat durations. Edit the beats to change it." />
        </Field>
        <LoraListEditor label="Image LoRAs" entries={panel.image_loras} options={window.SB.loraOptions} listId="lora-img"
          onChange={(entries) => onPatchPanel({ image_loras: entries })} />
        <LoraListEditor label="Video LoRAs" entries={panel.video_loras} options={window.SB.loraOptions} listId="lora-vid"
          onChange={(entries) => onPatchPanel({ video_loras: entries })} />

        {panel.videos.length ? (
          <Field label="Video takes">
            <div className="ms-candidates-row">
              {panel.videos.map((v) => (
                <VideoTakeTile key={v.id} src={v.file_path}
                  title={'seed ' + (v.seed ?? '—') + ' · take ' + (v.variant_index + 1)}
                  onDelete={() => onPatchPanel({ videos: panel.videos.filter((x) => x.id !== v.id) })} />
              ))}
            </div>
          </Field>
        ) : null}

        <div className="ms-field">
          <label className="ms-label">
            Beats — {total.toFixed(1)}s
            {over ? <span style={{ color: 'var(--warn)', marginLeft: '0.5rem', textTransform: 'none', fontWeight: 600 }}>exceeds H3 15s clip cap</span> : null}
          </label>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {panel.beats.map((b, i) => {
              const keeper = b.images.find((im) => im.id === b.selected_image_id) || b.images[0]
              const job = jobFor(b.id)
              return (
                <BeatPill key={b.id} index={i + 1} action={b.action} duration={b.duration_s}
                  motion={b.camera_motion} isCut={!!b.is_cut} imageCount={b.images.length}
                  dialogCount={b.dialog.length} thumb={keeper ? keeper.file_path : null}
                  job={job ? <JobBadge state={job.state} layout="fill" error={job.error} /> : null}
                  selected={b.id === selectedBeatId} onClick={() => onSelectBeat(b.id)}
                  actions={<>
                    <IconButton glyph="↑" title="Move beat up" disabled={i === 0} onClick={(e) => { e.stopPropagation(); onMoveBeat(i, -1) }} />
                    <IconButton glyph="↓" title="Move beat down" disabled={i === panel.beats.length - 1} onClick={(e) => { e.stopPropagation(); onMoveBeat(i, 1) }} />
                    <IconButton glyph="✕" destructive title="Delete beat" onClick={(e) => e.stopPropagation()} />
                  </>} />
              )
            })}
            {panel.beats.length === 0 ? <span className="ms-hint">No beats yet.</span> : null}
          </div>
          <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
            <Button size="sm" onClick={onAddBeat}>+ Beat</Button>
            <Button size="sm" onClick={() => setRebeatConfirm(true)}>Re-beat shot</Button>
          </div>
          {rebeatConfirm ? (
            <ConfirmBanner message="Beats have generated images or locked prompts — recompose anyway?"
              actions={<>
                <Button variant="danger" size="sm" onClick={() => setRebeatConfirm(false)}>Continue</Button>
                <Button size="sm" onClick={() => setRebeatConfirm(false)}>Cancel</Button>
              </>} />
          ) : null}
        </div>
      </div>
    </div>
  )
}

Object.assign(window, { ShotDetail })
