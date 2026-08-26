import React from 'react'
const cx = (...a) => a.filter(Boolean).join(' ')

export function PanelTile({
  src,
  dimmed = false,
  caption,
  active = false,
  locked = false,
  videoCount = 0,
  job,
  onClick,
  onDoubleClick,
  onDelete,
  className,
  style,
  children,
}) {
  return (
    <div
      className={cx('ms-panel-tile', active && 'ms-panel-tile--active', className)}
      style={style}
      onClick={onClick}
      onDoubleClick={onDoubleClick}
    >
      {onDelete ? (
        <button
          type="button"
          className="ms-iconbtn ms-iconbtn--scrim ms-panel-tile__delete"
          title="Delete panel"
          onClick={(e) => {
            e.stopPropagation()
            onDelete(e)
          }}
        >
          ×
        </button>
      ) : null}
      <div className="ms-panel-tile__thumb">
        {src ? <img src={src} alt="" className={dimmed ? 'ms-dimmed' : undefined} /> : <div className="ms-panel-tile__thumb-empty" />}
        {locked ? (
          <span className="ms-panel-tile__lock" title="Prompt locked">
            {'\uD83D\uDD12'}
          </span>
        ) : null}
        {videoCount > 0 ? (
          <span className="ms-panel-tile__video" title={videoCount + ' video take(s) — double-click to play'}>
            {'\uD83C\uDFAC'}
          </span>
        ) : null}
        {job}
      </div>
      {children ?? <div className="ms-panel-tile__caption">{caption}</div>}
    </div>
  )
}
