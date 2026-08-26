const { Button, BoardRow } = window.MetascanDesignSystem_f2fbbd

function LandingScreen({ onOpen }) {
  const [showCreate, setShowCreate] = React.useState(false)
  const [deleteTarget, setDeleteTarget] = React.useState(null)
  const [boards, setBoards] = React.useState(window.SB.list)
  return (
    <div style={{ height: '100%', overflowY: 'auto', padding: '32px 20px' }}>
      <div style={{ maxWidth: 720, margin: '0 auto', padding: '0 0 60px' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
          <h2 style={{ margin: 0, fontSize: 20, color: 'var(--text-color)', fontWeight: 600 }}>Storyboards</h2>
          <div style={{ display: 'flex', gap: 10 }}>
            <Button variant="primary" size="lg" icon="pi-plus" onClick={() => setShowCreate(true)}>New storyboard</Button>
            <Button variant="secondary" size="lg">Workflow presets…</Button>
          </div>
        </div>
        {boards.length === 0 ? (
          <div className="ms-empty-state">No storyboards yet — create one and paste your scene text.</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {boards.map((b) => (
              <BoardRow key={b.id} name={b.name}
                meta={b.aspect_ratio + ' · ' + b.target_model + ' · updated ' + b.updated_at}
                actions={<>
                  <Button onClick={() => onOpen(b.id)}>Open</Button>
                  <Button variant="danger" onClick={() => setDeleteTarget(b)}>Delete</Button>
                </>} />
            ))}
          </div>
        )}
      </div>
      {showCreate ? (
        <window.NewStoryboardDialog onClose={() => setShowCreate(false)} onCreated={() => { setShowCreate(false); onOpen(1) }} />
      ) : null}
      {deleteTarget ? (
        <window.DeleteImagesDialog
          title={'Delete storyboard "' + deleteTarget.name + '"?'}
          message="Its scenes, panels, and library folder are removed. What should happen to the generated images?"
          onPurge={() => { setBoards(boards.filter((b) => b.id !== deleteTarget.id)); setDeleteTarget(null) }}
          onKeep={() => { setBoards(boards.filter((b) => b.id !== deleteTarget.id)); setDeleteTarget(null) }}
          onCancel={() => setDeleteTarget(null)} />
      ) : null}
    </div>
  )
}

Object.assign(window, { LandingScreen })
