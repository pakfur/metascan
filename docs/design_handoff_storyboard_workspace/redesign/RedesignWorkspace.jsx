const NS = window.MetascanDesignSystem_f2fbbd
const { Button, IconButton, Chip, Field, TextInput, Select, Textarea, Checkbox, SubjectChip, CandidateTile, VideoTakeTile, JobBadge, LoraListEditor, ScriptBlock, Dialog } = NS
const CAP = 15
const keeperOf = (b) => b.images.find((i) => i.id === b.selected_image_id) || b.images[0] || null
const shotSecs = (p) => p.beats.reduce((s, b) => s + b.duration_s, 0)

function OutlineRail({ tree, panelId, onSelect, jobs }) {
  return (
    <div style={{ padding: '10px 8px 24px' }}>
      {tree.scenes.map((s) => (
        <div key={s.id} style={{ marginBottom: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '5px 6px' }}>
            <span style={{ fontSize: 10, color: 'var(--text-color-secondary)' }}>▼</span>
            <span style={{ fontSize: 12, fontWeight: 600, flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.name}</span>
            <span style={{ fontSize: 11, color: 'var(--text-color-secondary)', fontVariantNumeric: 'tabular-nums' }}>{s.panels.length}</span>
            <IconButton variant="corner" glyph="✎" title="Edit scene" style={{ fontSize: 10 }} />
          </div>
          {s.panels.map((p, i) => {
            const k = p.beats[0] && keeperOf(p.beats[0])
            const active = p.id === panelId
            const job = p.beats.map((b) => jobs[b.id]).find(Boolean)
            return (
              <button key={p.id} type="button" onClick={() => onSelect(p.id)}
                style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%', padding: '5px 6px', marginBottom: 1, textAlign: 'left', cursor: 'pointer', fontFamily: 'inherit',
                  border: '1px solid ' + (active ? 'var(--primary-color)' : 'transparent'), borderRadius: 6,
                  background: active ? 'var(--primary-tint-10)' : 'transparent', color: 'var(--text-color)' }}>
                <span style={{ position: 'relative', flexShrink: 0, width: 34, height: 34, borderRadius: 4, overflow: 'hidden', background: 'var(--surface-ground)' }}>
                  {k ? <img src={k.file_path} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }} />
                    : <span className="ms-thumb-empty" />}
                  {job ? <JobBadge state={job.state} layout="fill" error={job.error} /> : null}
                </span>
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span style={{ display: 'block', fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{i + 1}. {p.action}</span>
                  <span style={{ display: 'block', fontSize: 10, color: 'var(--text-color-secondary)', fontVariantNumeric: 'tabular-nums' }}>
                    {p.beats.length} beats · {shotSecs(p).toFixed(1)}s{p.videos.length ? ' · 🎬' + p.videos.length : ''}
                  </span>
                </span>
              </button>
            )
          })}
          <Button variant="dashed" size="xs" style={{ margin: '4px 0 0 6px' }}>+ Shot</Button>
        </div>
      ))}
      <Button variant="dashed" size="xs" style={{ marginLeft: 6 }}>+ Scene</Button>
    </div>
  )
}

// Lifted from direction A: duration is spatial. Segment widths track each
// beat's share of the shot, the trailing gap is unused clip budget, and the
// whole strip is the shot's 15s cap at 1:1 scale.
function PacingStrip({ panel, selectedBeatId, onSelectBeat, jobs }) {
  const secs = shotSecs(panel)
  const over = secs > CAP
  const scale = over ? secs : CAP
  // Measured strip width, so a segment can decide whether its duration label
  // fits before rendering it — a 0.5s beat is only ~30px wide.
  const boxRef = React.useRef(null)
  const [stripW, setStripW] = React.useState(1040)
  React.useEffect(() => {
    const el = boxRef.current
    if (!el) return
    const measure = () => setStripW(el.clientWidth || 1040)
    measure()
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [])
  return (
    <div>
      <div ref={boxRef} style={{ display: 'flex', gap: 2, height: 48 }}>
        {panel.beats.map((b, i) => {
          const k = keeperOf(b)
          const on = b.id === selectedBeatId
          return (
            <button key={b.id} type="button" onClick={() => onSelectBeat(b.id)} title={b.action}
              style={{ position: 'relative', width: (b.duration_s / scale) * 100 + '%', minWidth: 30, padding: 0, overflow: 'hidden', cursor: 'pointer', flexShrink: 0,
                border: '2px solid ' + (on ? 'var(--primary-color)' : 'transparent'), borderRadius: 5,
                borderLeft: b.is_cut ? '3px solid var(--warn)' : undefined,
                background: 'var(--surface-ground)' }}>
              {k ? <img src={k.file_path} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block', opacity: b.selected_image_id ? 1 : 0.45 }} />
                : <span style={{ position: 'absolute', inset: 2, border: '1px dashed var(--surface-border)', borderRadius: 3 }} />}
              <span style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: 6, padding: '2px 4px', background: 'linear-gradient(transparent, rgba(0,0,0,0.7))', color: '#fff', fontSize: 10, fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap', overflow: 'hidden' }}>
                <span>{i + 1}</span>
                {(b.duration_s / scale) * stripW >= 44 ? <span>{b.duration_s.toFixed(1)}s</span> : null}
              </span>
              {jobs[b.id] ? <JobBadge state={jobs[b.id].state} layout="fill" error={jobs[b.id].error} /> : null}
            </button>
          )
        })}
        {!over && secs < CAP ? (
          <div title={(CAP - secs).toFixed(1) + 's of clip budget unused'}
            style={{ width: ((CAP - secs) / scale) * 100 + '%', borderRadius: 5, border: '1px dashed var(--surface-border)' }} />
        ) : null}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4 }}>
        <div style={{ flex: 1, height: 3, borderRadius: 2, background: 'var(--surface-hover)', overflow: 'hidden' }}>
          <div style={{ width: Math.min(100, (secs / CAP) * 100) + '%', height: '100%', background: over ? 'var(--warn)' : 'var(--primary-color)' }} />
        </div>
        <span style={{ fontSize: 11, color: over ? 'var(--warn)' : 'var(--text-color-secondary)', fontVariantNumeric: 'tabular-nums' }}>
          {secs.toFixed(1)}s / {CAP}s{over ? ' — exceeds H3 clip cap' : ''}
        </span>
      </div>
    </div>
  )
}

// The compiled video prompt is reference output, not a field you type in —
// so it lives behind a button, with the room to actually read it.
function VideoPromptDialog({ panel, index, onClose, onPatch }) {
  const [copied, setCopied] = React.useState(false)
  const copy = () => {
    if (navigator.clipboard && panel.video_prompt) navigator.clipboard.writeText(panel.video_prompt)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }
  const words = panel.video_prompt ? panel.video_prompt.trim().split(/\s+/).length : 0
  return (
    <Dialog size="md" onDismiss={onClose}
      actions={<>
        <Button variant="primary" size="lg">Compile</Button>
        <Button variant="secondary" size="lg" disabled={!panel.video_prompt} onClick={copy}>{copied ? 'Copied' : 'Copy'}</Button>
        <Button variant="secondary" size="lg" disabled={!panel.video_prompt}>Render video</Button>
        <Button variant="secondary" size="lg" onClick={onClose}>Close</Button>
      </>}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
        <h3 className="ms-dialog__title" style={{ margin: 0 }}>Video prompt — shot {index + 1}</h3>
        <Chip tone="primary">MiniMax H3 · {window.SB.tree.video_mode}</Chip>
        <span className="ms-hint" style={{ marginLeft: 'auto', fontVariantNumeric: 'tabular-nums' }}>
          {panel.video_prompt_source ?? 'not compiled'}{words ? ' · ' + words + ' words' : ''}
        </span>
        {panel.video_prompt_locked === 1 ? <Button variant="link" onClick={() => onPatch({ video_prompt_locked: 0 })}>🔒 Unlock</Button> : null}
      </div>
      {panel.video_prompt_warnings.length ? (
        <ul style={{ margin: '0 0 10px', padding: '8px 10px 8px 26px', listStyle: 'disc', color: 'var(--warn)', fontSize: 12, lineHeight: 1.5, background: 'var(--warn-tint-14)', borderRadius: 6 }}>
          {panel.video_prompt_warnings.map((w, i) => <li key={i}>{w}</li>)}
        </ul>
      ) : null}
      {panel.video_prompt
        ? <ScriptBlock text={panel.video_prompt} style={{ maxHeight: '52vh', fontSize: 13, lineHeight: 1.6, padding: 14 }} />
        : <p className="ms-hint" style={{ fontSize: 13, padding: '24px 0', textAlign: 'center' }}>No video prompt compiled yet — Compile builds it from this shot's beats.</p>}
      <p className="ms-hint" style={{ fontSize: 11, marginTop: 10 }}>
        Compiled from the beats below. Edit a beat's framing, cast or dialog and recompile — hand-editing this text locks it against the next compile pass.
      </p>
    </Dialog>
  )
}

// The still prompt gets the same treatment as the video prompt when you want
// room — but this one IS a field, so the dialog is editable.
function BeatPromptDialog({ beat, index, onClose, onPatch }) {
  const [draft, setDraft] = React.useState(beat.prompt ?? '')
  const [copied, setCopied] = React.useState(false)
  const words = draft.trim() ? draft.trim().split(/\s+/).length : 0
  const dirty = draft !== (beat.prompt ?? '')
  const status = beat.prompt_locked === 1 ? '🔒 edited' : beat.prompt_source === 'llm' ? 'synthesized' : beat.prompt_source === 'brief' ? 'brief fallback' : 'not synthesized'
  const commit = () => {
    if (dirty) onPatch({ prompt: draft, prompt_source: 'user', prompt_locked: 1 })
    onClose()
  }
  const copy = () => {
    if (navigator.clipboard && draft) navigator.clipboard.writeText(draft)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }
  return (
    <Dialog size="md" onDismiss={onClose}
      actions={<>
        <Button variant="primary" size="lg" onClick={commit}>{dirty ? 'Save prompt' : 'Done'}</Button>
        <Button variant="secondary" size="lg" disabled={!draft} onClick={copy}>{copied ? 'Copied' : 'Copy'}</Button>
        <Button variant="secondary" size="lg">Re-synth</Button>
        <Button variant="secondary" size="lg" onClick={onClose}>Cancel</Button>
      </>}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
        <h3 className="ms-dialog__title" style={{ margin: 0 }}>Prompt — beat {index + 1}</h3>
        <span className="ms-hint">{status}</span>
        <span className="ms-hint" style={{ marginLeft: 'auto', fontVariantNumeric: 'tabular-nums' }}>{words} words</span>
        {beat.prompt_locked === 1 ? <Button variant="link" onClick={() => onPatch({ prompt_locked: 0 })}>Unlock</Button> : null}
      </div>
      <p className="ms-hint" style={{ fontSize: 12, marginBottom: 8 }}>
        {(beat.shot_size || '—') + ' · ' + (beat.angle || '—') + ' · ' + (beat.lens || '—') + (beat.camera_motion ? ' · ' + beat.camera_motion.replace(/_/g, ' ') : '')} — {beat.action}
      </p>
      <Textarea mono rows={16} value={draft} placeholder="No prompt synthesized yet."
        style={{ fontSize: 13, lineHeight: 1.6, padding: 14 }} onChange={(e) => setDraft(e.target.value)} />
      <p className="ms-hint" style={{ fontSize: 11, marginTop: 10 }}>
        Saving marks the prompt user-edited and locks it, so the next Synthesize pass leaves it alone.
      </p>
    </Dialog>
  )
}

function BeatCard({ beat, index, tree, jobs, onPatch, onRemove, canUp, canDown, onMove, selected, onSelect, cardRef }) {
  const SB = window.SB
  const name = (id) => (tree.subjects.find((s) => s.id === id) || {}).name || ('#' + id)
  const job = jobs[beat.id]
  const status = beat.prompt_locked === 1 ? '🔒 edited' : beat.prompt_source === 'llm' ? 'synthesized' : beat.prompt_source === 'brief' ? 'brief fallback' : '—'
  const [promptOpen, setPromptOpen] = React.useState(false)
  return (
    <article ref={cardRef} onClick={onSelect}
      style={{ border: '1px solid ' + (selected ? 'var(--primary-color)' : 'var(--surface-border)'),
        boxShadow: selected ? 'var(--ring-selected)' : 'none',
        borderRadius: 8, background: 'var(--surface-card)', padding: '12px 14px 14px', display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontSize: 12, fontWeight: 600, color: selected ? 'var(--primary-color)' : 'var(--text-color-secondary)' }}>BEAT {index + 1}</span>
        {beat.is_cut ? <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--warn)' }}>hard cut</span> : null}
        <span style={{ fontSize: 11, color: 'var(--text-color-secondary)', fontVariantNumeric: 'tabular-nums' }}>{beat.duration_s.toFixed(1)}s</span>
        {job ? <Chip>{job.state}</Chip> : null}
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 4 }}>
          <IconButton glyph="↑" title="Move beat up" disabled={!canUp} onClick={(e) => { e.stopPropagation(); onMove(-1) }} />
          <IconButton glyph="↓" title="Move beat down" disabled={!canDown} onClick={(e) => { e.stopPropagation(); onMove(1) }} />
          <IconButton glyph="✕" destructive title="Delete beat" onClick={(e) => { e.stopPropagation(); onRemove() }} />
        </span>
      </div>

      <div style={{ display: 'flex', gap: 14, alignItems: 'flex-start' }}>
        <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 10 }}>
          <Field label="Action"><Textarea rows={2} value={beat.action} onChange={(e) => onPatch({ action: e.target.value })} /></Field>
          <Field row>
            <Field label="Shot size"><Select options={SB.SHOT_SIZES} value={beat.shot_size ?? ''} onChange={(e) => onPatch({ shot_size: e.target.value || null })} /></Field>
            <Field label="Angle"><Select options={SB.ANGLES} value={beat.angle ?? ''} onChange={(e) => onPatch({ angle: e.target.value || null })} /></Field>
            <Field label="Lens"><Select options={SB.LENSES} value={beat.lens ?? ''} onChange={(e) => onPatch({ lens: e.target.value || null })} /></Field>
            <Field label="Dur (s)" style={{ flex: '0 0 84px' }}>
              <TextInput type="number" step="0.5" min="0.5" value={beat.duration_s} onChange={(e) => onPatch({ duration_s: Number(e.target.value) })} />
            </Field>
            <Button size="md" active={!!beat.is_cut} onClick={() => onPatch({ is_cut: beat.is_cut ? 0 : 1 })} title="Toggle hard cut before this beat">Cut</Button>
          </Field>
          <Field label="Camera move">
            <div style={{ display: 'flex', gap: 6 }}>
              <Select style={{ flex: 2 }} options={SB.CAMERA_MOTIONS} value={beat.camera_motion ?? ''} onChange={(e) => onPatch({ camera_motion: e.target.value || null })} />
              <Select style={{ flex: 1 }} includeEmpty={false} options={SB.CAMERA_AMPLITUDES} value={beat.camera_amplitude ?? 'small'} onChange={(e) => onPatch({ camera_amplitude: e.target.value })} />
              <Select style={{ flex: 1 }} includeEmpty={false} options={SB.CAMERA_SPEEDS} value={beat.camera_speed ?? 'slow'} onChange={(e) => onPatch({ camera_speed: e.target.value })} />
            </div>
          </Field>
          <Field label="Cast">
            {beat.subject_ids.length ? (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 2 }}>
                {beat.subject_ids.map((sid, i) => (
                  <SubjectChip key={sid} name={name(sid)} primary={i === 0}
                    onClick={() => onPatch({ subject_ids: [sid].concat(beat.subject_ids.filter((x) => x !== sid)) })} />
                ))}
              </div>
            ) : null}
            <div className="ms-checklist">
              {tree.subjects.map((s) => (
                <Checkbox key={s.id} label={s.name} checked={beat.subject_ids.includes(s.id)}
                  onChange={() => onPatch({ subject_ids: beat.subject_ids.includes(s.id) ? beat.subject_ids.filter((x) => x !== s.id) : beat.subject_ids.concat(s.id) })} />
              ))}
            </div>
          </Field>
        </div>

        <div style={{ flex: '0 0 340px', display: 'flex', flexDirection: 'column', gap: 10 }}>
          <Field label="Prompt" aside={<>
            <span className="ms-hint" style={{ flex: 1 }}>{status}</span>
            {beat.prompt_locked === 1 ? <Button variant="link" onClick={() => onPatch({ prompt_locked: 0 })}>Unlock</Button> : null}
            <IconButton icon="pi-window-maximize" title="Open prompt in a larger editor"
              onClick={(e) => { e.stopPropagation(); setPromptOpen(true) }} />
          </>}>
            <Textarea rows={6} value={beat.prompt ?? ''} placeholder="No prompt synthesized yet."
              onChange={(e) => onPatch({ prompt: e.target.value, prompt_source: 'user', prompt_locked: 1 })} />
          </Field>
          <div className="ms-field">
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <label className="ms-label" style={{ flex: 1 }}>Candidates</label>
              <Button size="xs">Re-synth</Button>
              <Button size="xs">Reroll</Button>
            </div>
            <div className="ms-candidates-row">
              {beat.images.map((img) => (
                <CandidateTile key={img.id} src={img.file_path} selected={img.id === beat.selected_image_id}
                  title={'seed ' + (img.seed ?? '—') + ' · variant ' + img.variant_index}
                  onClick={() => onPatch({ selected_image_id: beat.selected_image_id === img.id ? null : img.id })} onExpand={() => {}} />
              ))}
              {beat.images.length === 0 ? <div className="ms-hint" style={{ padding: '8px 0', fontSize: 12 }}>No candidates yet.</div> : null}
            </div>
          </div>
          <Field label="Sound"><Textarea rows={2} value={beat.sound ?? ''} placeholder="ambient, effects, music" onChange={(e) => onPatch({ sound: e.target.value })} /></Field>
          <Field label="Dialog">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, paddingLeft: 8, borderLeft: '2px solid var(--surface-border)' }}>
              {beat.dialog.map((l, i) => (
                <div key={i} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  <div style={{ display: 'flex', gap: 6 }}>
                    <Select style={{ flex: 1 }} includeEmpty={false} value={l.subject_id ?? ''}
                      options={[{ value: '', label: 'other voice' }].concat(tree.subjects.map((s) => ({ value: s.id, label: s.name })))}
                      onChange={(e) => onPatch({ dialog: beat.dialog.map((x, j) => (j === i ? { ...x, subject_id: e.target.value === '' ? null : Number(e.target.value) } : x)) })} />
                    <TextInput style={{ flex: '0 0 84px' }} placeholder="delivery" defaultValue={l.delivery ?? ''} />
                    <IconButton size="lg" glyph="✕" title="Remove line" onClick={() => onPatch({ dialog: beat.dialog.filter((_, j) => j !== i) })} />
                  </div>
                  <Textarea rows={2} placeholder="spoken line" defaultValue={l.text} />
                </div>
              ))}
              <Button variant="dashed" size="xs" style={{ alignSelf: 'flex-start' }}
                onClick={() => onPatch({ dialog: beat.dialog.concat({ subject_id: null, voice: null, delivery: null, language: 'English', text: '' }) })}>+ line</Button>
            </div>
          </Field>
        </div>
      </div>
      {promptOpen ? (
        <BeatPromptDialog beat={beat} index={index} onClose={() => setPromptOpen(false)} onPatch={onPatch} />
      ) : null}
    </article>
  )
}

function ShotHeader({ panel, index, sceneName, onPatch, selectedBeatId, onSelectBeat, jobs, onOpenPrompt }) {
  const SB = window.SB
  const warn = panel.video_prompt_warnings.length
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, paddingBottom: 16, borderBottom: '1px solid var(--surface-border)' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
        <span className="ms-hint">{sceneName} ›</span>
        <h3 style={{ margin: 0, fontSize: 18, fontWeight: 600 }}>Shot {index + 1}</h3>
        <span className="ms-hint">{panel.beats.length} beats</span>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          <Button size="sm">Reroll shot</Button>
          <Button size="sm">Re-synth shot</Button>
          <Button size="sm">Re-beat shot</Button>
        </div>
      </div>

      <PacingStrip panel={panel} selectedBeatId={selectedBeatId} onSelectBeat={onSelectBeat} jobs={jobs} />

      <div style={{ display: 'flex', gap: 16, alignItems: 'flex-end' }}>
        <Field label="Shot action" style={{ flex: 1, minWidth: 220 }}>
          <TextInput value={panel.action} onChange={(e) => onPatch({ action: e.target.value })} />
        </Field>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0 }}>
          <Button size="sm" icon="pi-file" onClick={onOpenPrompt}>Video prompt</Button>
          <span className="ms-hint">{panel.video_prompt_source ?? 'not compiled'}</span>
          {panel.video_prompt_locked === 1 ? <span style={{ fontSize: 11 }} title="Locked against the next compile">🔒</span> : null}
          {warn ? <Chip tone="warn">{warn} lint {warn === 1 ? 'warning' : 'warnings'}</Chip> : null}
          <Button size="sm">Compile</Button>
          <Button size="sm" variant="primary" disabled={!panel.video_prompt} icon="pi-play">Render video</Button>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start' }}>
        <div style={{ flex: 1, minWidth: 236 }}>
          <LoraListEditor label="Image LoRAs" entries={panel.image_loras} options={SB.loraOptions} listId="ws-img"
            onChange={(entries) => onPatch({ image_loras: entries })} />
        </div>
        <div style={{ flex: 1, minWidth: 236 }}>
          <LoraListEditor label="Video LoRAs" entries={panel.video_loras} options={SB.loraOptions} listId="ws-vid"
            onChange={(entries) => onPatch({ video_loras: entries })} />
        </div>
        <Field label={'Takes (' + panel.videos.length + ')'} style={{ flex: '0 0 374px' }}
          aside={panel.videos.length ? <span className="ms-hint">newest last · double-click to play</span> : null}>
          {panel.videos.length ? (
            <div className="ms-candidates-row">
              {panel.videos.map((v) => (
                <VideoTakeTile key={v.id} src={v.file_path} style={{ width: 176, height: 99 }}
                  title={'seed ' + v.seed + ' · take ' + (v.variant_index + 1)} onDelete={() => {}} />
              ))}
            </div>
          ) : (
            <div style={{ height: 99, display: 'flex', alignItems: 'center', justifyContent: 'center', border: '1px dashed var(--surface-border)', borderRadius: 6, color: 'var(--text-color-secondary)', fontSize: 12 }}>
              No takes rendered yet.
            </div>
          )}
        </Field>
      </div>
    </div>
  )
}

function RedesignWorkspace() {
  const SB = window.SB
  const [tree, setTree] = React.useState(SB.tree)
  const [panelId, setPanelId] = React.useState(11)
  const [beatId, setBeatId] = React.useState(101)
  const [promptOpen, setPromptOpen] = React.useState(false)
  const scrollRef = React.useRef(null)
  const cardRefs = React.useRef({})
  const jobs = { 105: { state: 'running' }, 202: { state: 'queued' }, 301: { state: 'failed', error: 'preset 3: node 12 missing input' } }
  const scene = tree.scenes.find((s) => s.panels.some((p) => p.id === panelId))
  const panel = scene && scene.panels.find((p) => p.id === panelId)
  const index = scene ? scene.panels.indexOf(panel) : 0
  const mutate = (fn) => setTree((t) => { const n = JSON.parse(JSON.stringify(t)); fn(n); return n })
  const patchPanel = (body) => mutate((t) => { const p = t.scenes.flatMap((s) => s.panels).find((x) => x.id === panelId); if (p) Object.assign(p, body) })
  const patchBeat = (id, body) => mutate((t) => { const b = t.scenes.flatMap((s) => s.panels).flatMap((p) => p.beats).find((x) => x.id === id); if (b) Object.assign(b, body) })
  const moveBeat = (i, dir) => mutate((t) => {
    const p = t.scenes.flatMap((s) => s.panels).find((x) => x.id === panelId)
    const j = i + dir
    if (!p || j < 0 || j >= p.beats.length) return
    const tmp = p.beats[i]; p.beats[i] = p.beats[j]; p.beats[j] = tmp
  })
  const removeBeat = (id) => mutate((t) => { const p = t.scenes.flatMap((s) => s.panels).find((x) => x.id === panelId); if (p) p.beats = p.beats.filter((b) => b.id !== id) })
  const addBeat = () => {
    const id = Date.now()
    mutate((t) => {
      const p = t.scenes.flatMap((s) => s.panels).find((x) => x.id === panelId)
      if (p) p.beats.push({ id, duration_s: 2, action: 'new beat', shot_size: null, angle: null, lens: null, subject_ids: [], camera_motion: null, camera_amplitude: null, camera_speed: null, is_cut: 0, dialog: [], sound: null, prompt: null, prompt_locked: 0, prompt_source: null, selected_image_id: null, images: [] })
    })
    setBeatId(id)
  }
  const selectShot = (id) => {
    setPanelId(id)
    const p = tree.scenes.flatMap((s) => s.panels).find((x) => x.id === id)
    setBeatId(p && p.beats[0] ? p.beats[0].id : null)
    if (scrollRef.current) scrollRef.current.scrollTop = 0
  }
  // Selecting from the pacing strip brings that beat's card up without
  // scrollIntoView: the scroll container owns the offset maths.
  const selectBeat = (id) => {
    setBeatId(id)
    const el = cardRefs.current[id]
    const box = scrollRef.current
    if (el && box) box.scrollTop = Math.max(0, el.offsetTop - box.offsetTop - 12)
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <header style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 20px', borderBottom: '1px solid var(--surface-border)', flexShrink: 0 }}>
        <a href="#" onClick={(e) => e.preventDefault()} style={{ color: 'var(--text-color-secondary)', fontSize: 13 }}>← Library</a>
        <h2 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>{tree.name}</h2>
        <Chip tone="primary">MiniMax H3 · {tree.video_mode}</Chip>
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 6 }}>
          <Button variant="quiet" icon="pi-sparkles">Compose</Button>
          <Button variant="primary" icon="pi-play">Generate all</Button>
          <IconButton variant="outline" size="lg" glyph="⋯" title="Import text, compile, render, cancel" />
          <IconButton variant="outline" size="lg" icon="pi-cog" title="Storyboard settings" />
        </div>
      </header>
      <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
        <aside style={{ flex: '0 0 270px', minHeight: 0, overflowY: 'auto', borderRight: '1px solid var(--surface-border)' }}>
          <OutlineRail tree={tree} panelId={panelId} onSelect={selectShot} jobs={jobs} />
        </aside>
        <main ref={scrollRef} style={{ flex: 1, minWidth: 0, minHeight: 0, overflowY: 'auto', padding: '18px 24px 40px' }}>
          {panel ? (
            <div style={{ maxWidth: 1040, display: 'flex', flexDirection: 'column', gap: 16 }}>
              <ShotHeader panel={panel} index={index} sceneName={scene.name} onPatch={patchPanel}
                selectedBeatId={beatId} onSelectBeat={selectBeat} jobs={jobs} onOpenPrompt={() => setPromptOpen(true)} />
              {panel.beats.map((b, i) => (
                <BeatCard key={b.id} beat={b} index={i} tree={tree} jobs={jobs}
                  cardRef={(el) => { cardRefs.current[b.id] = el }}
                  selected={b.id === beatId} onSelect={() => setBeatId(b.id)}
                  onPatch={(body) => patchBeat(b.id, body)} onRemove={() => removeBeat(b.id)}
                  canUp={i > 0} canDown={i < panel.beats.length - 1} onMove={(d) => moveBeat(i, d)} />
              ))}
              <Button variant="dashed" size="md" style={{ alignSelf: 'flex-start' }} onClick={addBeat}>+ Beat</Button>
            </div>
          ) : <p className="ms-hint" style={{ fontSize: 14 }}>Select a shot from the outline.</p>}
        </main>
      </div>
      {promptOpen && panel ? (
        <VideoPromptDialog panel={panel} index={index} onClose={() => setPromptOpen(false)} onPatch={patchPanel} />
      ) : null}
    </div>
  )
}

Object.assign(window, { RedesignWorkspace })
