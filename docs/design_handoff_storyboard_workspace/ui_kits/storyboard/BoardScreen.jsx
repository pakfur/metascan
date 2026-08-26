const { Button, IconButton, Chip, TextInput, SceneCard, PanelTile, AddTile, JobBadge } = window.MetascanDesignSystem_f2fbbd

function BoardScreen({ onBack }) {
  const SB = window.SB
  const [tree, setTree] = React.useState(SB.tree)
  const [sceneId, setSceneId] = React.useState(1)
  const [panelId, setPanelId] = React.useState(11)
  const [beatId, setBeatId] = React.useState(101)
  const [dialog, setDialog] = React.useState(null)
  const [adding, setAdding] = React.useState(false)
  const [newAction, setNewAction] = React.useState('')
  const [jobs, setJobs] = React.useState({ 105: { state: 'running', value: 14, max: 20 }, 202: { state: 'queued' }, 301: { state: 'failed', error: 'preset 3: node 12 missing input' } })
  const [error, setError] = React.useState(null)
  const [busy, setBusy] = React.useState(null)

  const scene = tree.scenes.find((s) => s.id === sceneId)
  const panel = scene && scene.panels.find((p) => p.id === panelId)
  const beat = panel && panel.beats.find((b) => b.id === beatId)
  const jobFor = (id) => jobs[id]

  const mutate = (fn) => setTree((t) => {
    const next = JSON.parse(JSON.stringify(t))
    fn(next)
    return next
  })
  const patchPanel = (body) => mutate((t) => {
    const p = t.scenes.flatMap((s) => s.panels).find((x) => x.id === panelId)
    if (p) Object.assign(p, body)
  })
  const patchBeat = (body) => mutate((t) => {
    const b = t.scenes.flatMap((s) => s.panels).flatMap((p) => p.beats).find((x) => x.id === beatId)
    if (b) Object.assign(b, body)
  })
  const moveBeat = (i, dir) => mutate((t) => {
    const p = t.scenes.flatMap((s) => s.panels).find((x) => x.id === panelId)
    if (!p) return
    const j = i + dir
    if (j < 0 || j >= p.beats.length) return
    const tmp = p.beats[i]
    p.beats[i] = p.beats[j]
    p.beats[j] = tmp
  })
  const addBeat = () => {
    const id = Date.now()
    mutate((t) => {
      const p = t.scenes.flatMap((s) => s.panels).find((x) => x.id === panelId)
      if (p) p.beats.push({ id, duration_s: 2, action: 'new beat', shot_size: null, angle: null, lens: null, subject_ids: [], camera_motion: null, camera_amplitude: null, camera_speed: null, is_cut: 0, dialog: [], sound: null, prompt: null, prompt_locked: 0, prompt_source: null, selected_image_id: null, images: [] })
    })
    setBeatId(id)
  }
  const selectScene = (s) => { setSceneId(s.id); const first = s.panels[0]; setPanelId(first ? first.id : null); setBeatId(first && first.beats[0] ? first.beats[0].id : null) }
  const selectPanel = (p) => { setPanelId(p.id); setBeatId(p.beats[0] ? p.beats[0].id : null) }
  const submitPanel = () => {
    const action = newAction.trim()
    if (!action) return
    const id = Date.now()
    mutate((t) => {
      const s = t.scenes.find((x) => x.id === sceneId)
      if (s) s.panels.push({ id, sort_order: s.panels.length, action, duration_s: 2, image_loras: [], video_loras: [], video_prompt: null, video_prompt_source: null, video_prompt_locked: 0, video_prompt_warnings: [], video_anchor: null, video_compiled_anchor: null, videos: [], beats: [] })
    })
    setAdding(false); setNewAction(''); setPanelId(id); setBeatId(null)
  }
  const runFakeJob = () => {
    setBusy({ label: 'synthesizing', done: 0, total: 12 })
    let n = 0
    const t = setInterval(() => {
      n += 3
      if (n >= 12) { clearInterval(t); setBusy(null); setJobs({}) } else setBusy({ label: 'synthesizing', done: n, total: 12 })
    }, 600)
  }

  const caption = (p) => {
    const b = p.beats[0]
    if (!b) return '—'
    const names = b.subject_ids.map((id) => (tree.subjects.find((s) => s.id === id) || {}).name).filter(Boolean).join(', ')
    return names ? (b.shot_size || '—') + ' · ' + names : (b.shot_size || '—')
  }
  const keeper = (p) => {
    const b = p.beats[0]
    if (!b) return null
    const img = b.images.find((im) => im.id === b.selected_image_id)
    return img ? img.file_path : (b.images[0] ? b.images[0].file_path : null)
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <header style={{ display: 'flex', alignItems: 'center', gap: 16, padding: '12px 20px', borderBottom: '1px solid var(--surface-border)', flexShrink: 0 }}>
        <a href="#" onClick={(e) => { e.preventDefault(); onBack() }} style={{ color: 'var(--text-color-secondary)', fontSize: 13, textDecoration: 'none', flexShrink: 0 }}>← Library</a>
        <h2 style={{ margin: 0, fontSize: 16, color: 'var(--text-color)', flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontWeight: 600 }}>{tree.name}</h2>
        <Chip tone="primary" title="Video model and mode driving the compiled video prompts — change in Storyboard settings">MiniMax H3 · {tree.video_mode}</Chip>
        {busy ? <Chip>{busy.label} {busy.done}/{busy.total}</Chip> : null}
        {error ? <Chip tone="danger" title={error} onDismiss={() => setError(null)}>{error}</Chip> : null}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
          <Button variant="quiet" icon="pi-file-import" onClick={() => setDialog({ kind: 'import' })}>Import text</Button>
          <Button variant="quiet" icon="pi-sparkles" onClick={() => setDialog({ kind: 'compose' })}>Compose</Button>
          <Button variant="quiet" onClick={runFakeJob}>Synthesize</Button>
          <Button variant="quiet">Compile video prompts</Button>
          <Button variant="primary" onClick={runFakeJob}>Generate all</Button>
          <Button variant="quiet">Generate video</Button>
          <Button variant="quiet" style={{ color: 'var(--danger-color)' }} onClick={() => setError('panel 31: comfy queue rejected the prompt')}>Cancel</Button>
          <IconButton variant="outline" size="lg" icon="pi-cog" title="Storyboard settings" onClick={() => setDialog({ kind: 'settings' })} />
        </div>
      </header>

      <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
        <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minWidth: 0, minHeight: 0 }}>
          <div style={{ display: 'flex', gap: 10, padding: '12px 20px', overflowX: 'auto', borderBottom: '1px solid var(--surface-border)', flexShrink: 0 }}>
            {tree.scenes.map((s) => (
              <SceneCard key={s.id} name={s.name} subtitle={s.subtitle} setting={s.setting}
                thumbs={s.panels.slice(0, 6).map(keeper)} active={s.id === sceneId}
                onClick={() => selectScene(s)}
                actions={<>
                  <IconButton variant="corner" glyph="▶" title="Render scene videos" style={{ fontSize: 8 }} onClick={(e) => e.stopPropagation()} />
                  <IconButton variant="corner" glyph="✎" title="Edit scene" style={{ fontSize: 10 }} onClick={(e) => { e.stopPropagation(); setDialog({ kind: 'scene', scene: s }) }} />
                  <IconButton variant="corner" glyph="×" destructive title="Delete scene" onClick={(e) => { e.stopPropagation(); setDialog({ kind: 'deleteScene', scene: s }) }} />
                </>} />
            ))}
            <AddTile kind="scene" label="Scene" onClick={() => setDialog({ kind: 'scene', scene: null })} />
          </div>

          <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '16px 20px', display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: 14, alignContent: 'start' }}>
            {scene ? scene.panels.map((p) => {
              const job = p.beats.map((b) => jobs[b.id]).find(Boolean)
              return (
                <PanelTile key={p.id} src={keeper(p)} dimmed={!!p.beats[0] && !p.beats[0].selected_image_id}
                  caption={caption(p)} active={p.id === panelId}
                  locked={p.beats[0] && p.beats[0].prompt_locked === 1}
                  videoCount={p.videos.length}
                  job={job ? <JobBadge state={job.state} value={job.value} max={job.max} error={job.error} /> : null}
                  onClick={() => selectPanel(p)}
                  onDelete={() => setDialog({ kind: 'deletePanel', panel: p })} />
              )
            }) : <div className="ms-hint" style={{ gridColumn: '1 / -1', fontSize: 14, padding: '24px 0' }}>Select a scene to see its panels.</div>}
            <AddTile kind="panel" label="Panel" onClick={() => setAdding(true)}>
              {adding ? (
                <>
                  <TextInput autoFocus placeholder="Action" value={newAction} style={{ fontSize: 12, padding: '5px 8px' }}
                    onClick={(e) => e.stopPropagation()} onChange={(e) => setNewAction(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter') submitPanel(); if (e.key === 'Escape') { setAdding(false); setNewAction('') } }} />
                  <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
                    <Button size="xs" disabled={!newAction.trim()} onClick={(e) => { e.stopPropagation(); submitPanel() }}>Add</Button>
                    <Button size="xs" onClick={(e) => { e.stopPropagation(); setAdding(false); setNewAction('') }}>Cancel</Button>
                  </div>
                </>
              ) : null}
            </AddTile>
          </div>

          {panel ? (
            <>
              <div title="Drag to resize" style={{ position: 'relative', zIndex: 5, flexShrink: 0, height: 7, margin: '-3px 0', cursor: 'row-resize' }} />
              <window.ShotDetail panel={panel} subjects={tree.subjects} selectedBeatId={beatId}
                onSelectBeat={setBeatId} onPatchPanel={patchPanel} onMoveBeat={moveBeat} onAddBeat={addBeat} jobFor={jobFor} />
            </>
          ) : null}
        </div>

        {panel ? (
          <window.SidePanel panel={panel} scene={scene} beat={beat} subjects={tree.subjects}
            selectedBeatId={beatId} onSelectBeat={setBeatId} onPatchBeat={patchBeat} onPatchPanel={patchPanel} />
        ) : null}
      </div>

      {dialog && dialog.kind === 'compose' ? <window.ComposeDialog onClose={() => setDialog(null)} /> : null}
      {dialog && dialog.kind === 'scene' ? <window.SceneEditDialog scene={dialog.scene} onClose={() => setDialog(null)} /> : null}
      {dialog && dialog.kind === 'import' ? <window.ImportTextDialog onClose={() => setDialog(null)} /> : null}
      {dialog && dialog.kind === 'settings' ? <window.SettingsDialog onClose={() => setDialog(null)} /> : null}
      {dialog && dialog.kind === 'deletePanel' ? (
        <window.DeleteImagesDialog title="Delete panel?"
          message="This panel has generated images. Delete them permanently, or keep them visible in the media library?"
          imageCount={dialog.panel.videos.length + dialog.panel.beats.reduce((n, b) => n + b.images.length, 0)}
          onPurge={() => { mutate((t) => { const s = t.scenes.find((x) => x.id === sceneId); s.panels = s.panels.filter((p) => p.id !== dialog.panel.id) }); setDialog(null); setPanelId(null) }}
          onKeep={() => { mutate((t) => { const s = t.scenes.find((x) => x.id === sceneId); s.panels = s.panels.filter((p) => p.id !== dialog.panel.id) }); setDialog(null); setPanelId(null) }}
          onCancel={() => setDialog(null)} />
      ) : null}
      {dialog && dialog.kind === 'deleteScene' ? (
        <window.DeleteImagesDialog title={'Delete scene "' + dialog.scene.name + '"?'}
          message="Its panels have generated images. Delete them permanently, or keep them visible in the media library?"
          imageCount={dialog.scene.panels.reduce((n, p) => n + p.videos.length + p.beats.reduce((m, b) => m + b.images.length, 0), 0)}
          onPurge={() => { mutate((t) => { t.scenes = t.scenes.filter((s) => s.id !== dialog.scene.id) }); setDialog(null); setSceneId(tree.scenes[0].id) }}
          onKeep={() => { mutate((t) => { t.scenes = t.scenes.filter((s) => s.id !== dialog.scene.id) }); setDialog(null); setSceneId(tree.scenes[0].id) }}
          onCancel={() => setDialog(null)} />
      ) : null}
    </div>
  )
}

Object.assign(window, { BoardScreen })
