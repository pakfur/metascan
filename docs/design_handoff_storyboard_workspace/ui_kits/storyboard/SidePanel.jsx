const { Button, IconButton, Field, TextInput, Select, Textarea, Checkbox, Chip, Tabs, SubjectChip, CandidateTile, ScriptBlock } = window.MetascanDesignSystem_f2fbbd

function BeatEditor({ beat, subjects, onPatch }) {
  const SB = window.SB
  const status = beat.prompt_locked === 1 ? '🔒 edited'
    : beat.prompt_source === 'brief' ? 'brief fallback'
    : beat.prompt_source === 'llm' ? 'synthesized' : '—'
  const name = (id) => (subjects.find((s) => s.id === id) || {}).name || ('#' + id)
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <Field label="Action"><Textarea rows={3} value={beat.action} onChange={(e) => onPatch({ action: e.target.value })} /></Field>

      <Field row>
        <Field label="Shot size"><Select options={SB.SHOT_SIZES} value={beat.shot_size ?? ''} onChange={(e) => onPatch({ shot_size: e.target.value || null })} /></Field>
        <Field label="Angle"><Select options={SB.ANGLES} value={beat.angle ?? ''} onChange={(e) => onPatch({ angle: e.target.value || null })} /></Field>
        <Field label="Lens"><Select options={SB.LENSES} value={beat.lens ?? ''} onChange={(e) => onPatch({ lens: e.target.value || null })} /></Field>
      </Field>

      <Field label="Subjects">
        {beat.subject_ids.length ? (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 4 }}>
            {beat.subject_ids.map((sid, i) => (
              <SubjectChip key={sid} name={name(sid)} primary={i === 0}
                onClick={() => onPatch({ subject_ids: [sid].concat(beat.subject_ids.filter((x) => x !== sid)) })} />
            ))}
          </div>
        ) : null}
        <div className="ms-checklist">
          {subjects.map((s) => (
            <Checkbox key={s.id} label={s.name} checked={beat.subject_ids.includes(s.id)}
              onChange={() => onPatch({
                subject_ids: beat.subject_ids.includes(s.id)
                  ? beat.subject_ids.filter((x) => x !== s.id)
                  : beat.subject_ids.concat(s.id),
              })} />
          ))}
        </div>
      </Field>

      <Field label="Prompt" aside={<>
        <span className="ms-hint" style={{ flex: 1 }}>{status}</span>
        {beat.prompt_locked === 1 ? <Button variant="link" onClick={() => onPatch({ prompt_locked: 0 })}>Unlock</Button> : null}
      </>}>
        <Textarea rows={4} value={beat.prompt ?? ''} placeholder="No prompt synthesized yet." onChange={(e) => onPatch({ prompt: e.target.value, prompt_source: 'user', prompt_locked: 1 })} />
        <Button size="xs" style={{ alignSelf: 'flex-start', marginTop: 6 }}>Re-synth prompt</Button>
      </Field>

      <div className="ms-field">
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
          <label className="ms-label">Candidates</label>
          <Button size="xs">Reroll</Button>
        </div>
        <div className="ms-candidates-row">
          {beat.images.map((img) => (
            <CandidateTile key={img.id} src={img.file_path} selected={img.id === beat.selected_image_id}
              title={'seed ' + (img.seed ?? '—') + ' · variant ' + img.variant_index}
              onClick={() => onPatch({ selected_image_id: beat.selected_image_id === img.id ? null : img.id })}
              onExpand={() => {}} />
          ))}
          {beat.images.length === 0 ? <div className="ms-hint" style={{ padding: '8px 0', fontSize: 12 }}>No candidates yet.</div> : null}
        </div>
      </div>

      <Field row>
        <Field label="Duration (s)" style={{ flex: '0 0 120px' }}>
          <TextInput type="number" step="0.5" min="0.5" value={beat.duration_s} onChange={(e) => onPatch({ duration_s: Number(e.target.value) })} />
        </Field>
        <Button size="md" active={!!beat.is_cut} title="Toggle hard cut before this beat"
          onClick={() => onPatch({ is_cut: beat.is_cut ? 0 : 1 })}>Cut</Button>
      </Field>

      <Field row>
        <Field label="Motion"><Select options={SB.CAMERA_MOTIONS} value={beat.camera_motion ?? ''} onChange={(e) => onPatch({ camera_motion: e.target.value || null })} /></Field>
        <Field label="Amplitude"><Select options={SB.CAMERA_AMPLITUDES} value={beat.camera_amplitude ?? ''} onChange={(e) => onPatch({ camera_amplitude: e.target.value || null })} /></Field>
        <Field label="Speed"><Select options={SB.CAMERA_SPEEDS} value={beat.camera_speed ?? ''} onChange={(e) => onPatch({ camera_speed: e.target.value || null })} /></Field>
      </Field>

      <Field label="Sound"><Textarea rows={2} value={beat.sound ?? ''} onChange={(e) => onPatch({ sound: e.target.value })} /></Field>

      <Field label="Dialog">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, paddingLeft: 8, borderLeft: '2px solid var(--surface-border)' }}>
          {beat.dialog.map((line, i) => (
            <div key={i} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <Select style={{ flex: '0 0 100px' }} includeEmpty={false} value={line.subject_id ?? ''}
                  options={[{ value: '', label: 'other voice' }].concat(subjects.map((s) => ({ value: s.id, label: s.name })))}
                  onChange={(e) => onPatch({ dialog: beat.dialog.map((l, j) => (j === i ? { ...l, subject_id: e.target.value === '' ? null : Number(e.target.value) } : l)) })} />
                {line.subject_id === null ? <TextInput style={{ flex: '0 0 80px' }} placeholder="voice" defaultValue={line.voice ?? ''} /> : null}
                <TextInput style={{ flex: '0 0 80px' }} placeholder="delivery" defaultValue={line.delivery ?? ''} />
                <TextInput style={{ flex: '0 0 72px' }} placeholder="language" defaultValue={line.language} />
                <IconButton size="lg" glyph="✕" title="Remove line"
                  onClick={() => onPatch({ dialog: beat.dialog.filter((_, j) => j !== i) })} />
              </div>
              <Textarea rows={2} placeholder="spoken line" defaultValue={line.text} />
            </div>
          ))}
          <Button variant="dashed" size="xs" style={{ alignSelf: 'flex-start' }}
            onClick={() => onPatch({ dialog: beat.dialog.concat({ subject_id: null, voice: null, delivery: null, language: 'English', text: '' }) })}>+ line</Button>
        </div>
      </Field>

      <Button variant="danger" size="md" style={{ alignSelf: 'flex-start' }}>Delete beat</Button>
    </div>
  )
}

function PreviewPane({ panel, scene, beat, subjects, selectedBeatId, onSelectBeat, onPatchPanel }) {
  const [copied, setCopied] = React.useState(null)
  const flash = (k) => { setCopied(k); setTimeout(() => setCopied(null), 1500) }
  const name = (id) => (subjects.find((s) => s.id === id) || {}).name || ('#' + id)
  const header = 'SHOT ' + ((panel.sort_order ?? 0) + 1) + ' — ' + scene.name.toUpperCase() + '\n'
    + (scene.location || '') + (scene.time_of_day ? ' · ' + scene.time_of_day : '')
    + (scene.lighting ? ' · ' + scene.lighting : '')
  const blocks = panel.beats.map((b, i) => ({
    beatId: b.id,
    text: '[Beat ' + (i + 1) + '] ' + b.duration_s.toFixed(1) + 's  '
      + (b.shot_size || '—') + ', ' + (b.angle || '—') + ', ' + (b.lens || '—')
      + (b.camera_motion ? ', ' + b.camera_motion.replace(/_/g, ' ') : '')
      + (b.is_cut ? '  (cut)' : '')
      + '\n  ' + b.action
      + (b.subject_ids.length ? '\n  cast: ' + b.subject_ids.map(name).join(', ') : '')
      + (b.dialog.length ? '\n  ' + b.dialog.map((l) => (l.subject_id ? name(l.subject_id) : (l.voice || 'voice')).toUpperCase() + (l.delivery ? ' (' + l.delivery + ')' : '') + ': ' + l.text).join('\n  ') : '')
      + (b.sound ? '\n  sound: ' + b.sound : ''),
  }))
  const anchorRelevant = window.SB.tree.video_mode === 'i2va' || window.SB.tree.video_mode === 'fl2va'
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <div className="ms-field" style={{ gap: 6 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <label className="ms-label">Video prompt</label>
          <span className="ms-hint" style={{ flex: 1 }}>{panel.video_prompt_source === 'user' ? 'user edited' : panel.video_prompt_source === 'compiled' ? 'compiled' : '—'}</span>
          {panel.video_anchor !== panel.video_compiled_anchor && anchorRelevant ? <Chip tone="warn">recompile suggested</Chip> : null}
          {panel.video_prompt_locked === 1 ? <Button variant="link" onClick={() => onPatchPanel({ video_prompt_locked: 0 })}>🔒 Unlock</Button> : null}
        </div>
        {panel.video_prompt_warnings.length ? (
          <ul style={{ margin: 0, padding: '0 0 0 16px', listStyle: 'disc', color: 'var(--warn)', fontSize: 11, lineHeight: 1.5 }}>
            {panel.video_prompt_warnings.map((w, i) => <li key={i}>{w}</li>)}
          </ul>
        ) : null}
        {anchorRelevant ? (
          <Field label="Anchor">
            <Select placeholder="None" value={panel.video_anchor ?? ''} onChange={(e) => onPatchPanel({ video_anchor: e.target.value || null })}
              options={[{ value: 'keeper', label: 'First frame from keeper' }, { value: 'prev_last', label: 'Continue from previous shot' }]} />
          </Field>
        ) : null}
        <Textarea rows={8} mono placeholder="No video prompt compiled yet." value={panel.video_prompt ?? ''}
          onChange={(e) => onPatchPanel({ video_prompt: e.target.value, video_prompt_source: 'user' })} />
        <div style={{ display: 'flex', gap: 8 }}>
          <Button size="xs">Compile</Button>
          <Button size="xs" disabled={!panel.video_prompt} onClick={() => flash('video')}>{copied === 'video' ? 'Copied' : 'Copy'}</Button>
          <Button size="xs" disabled={!panel.video_prompt}>Render video</Button>
          <Button size="xs" title="Render video for every shot in this scene">Render scene</Button>
        </div>
      </div>

      <Field label="Shot script" aside={<Button size="xs" onClick={() => flash('script')}>{copied === 'script' ? 'Copied' : 'Copy'}</Button>}>
        <ScriptBlock header={header} beats={blocks} selectedBeatId={selectedBeatId} onSelectBeat={onSelectBeat} />
      </Field>

      <Field label="Image prompt" aside={<Button size="xs" disabled={!beat || !beat.prompt} onClick={() => flash('prompt')}>{copied === 'prompt' ? 'Copied' : 'Copy'}</Button>}>
        {beat && beat.prompt ? <ScriptBlock text={beat.prompt} /> : <p className="ms-hint" style={{ fontSize: 12 }}>No prompt synthesized yet.</p>}
      </Field>
    </div>
  )
}

function SidePanel(props) {
  const [tab, setTab] = React.useState('edit')
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, borderLeft: '1px solid var(--surface-border)', background: 'var(--surface-card)', flex: '0 0 400px' }}>
      <Tabs tabs={[{ value: 'edit', label: 'Edit' }, { value: 'preview', label: 'Preview' }]} value={tab} onChange={setTab} />
      <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: 14 }}>
        {tab === 'edit'
          ? (props.beat
            ? <BeatEditor beat={props.beat} subjects={props.subjects} onPatch={props.onPatchBeat} />
            : <p className="ms-hint" style={{ fontSize: 12 }}>Select a beat to edit.</p>)
          : <PreviewPane {...props} />}
      </div>
    </div>
  )
}

Object.assign(window, { SidePanel, BeatEditor, PreviewPane })
