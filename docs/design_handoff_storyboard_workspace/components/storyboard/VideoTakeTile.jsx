import React from 'react'
const cx = (...a) => a.filter(Boolean).join(' ')

export function VideoTakeTile({ src, title, onPlay, onDelete, className, style }) {
  return (
    <div className={cx('ms-take', className)} style={style} title={title} onDoubleClick={onPlay}>
      <img src={src} alt="" />
      <span
        className="ms-take__play"
        title="Play"
        onClick={(e) => {
          e.stopPropagation()
          if (onPlay) onPlay(e)
        }}
      >
        {'\u25B6'}
      </span>
      {onDelete ? (
        <button
          type="button"
          className="ms-iconbtn ms-iconbtn--scrim ms-take__delete"
          title="Delete take"
          onClick={(e) => {
            e.stopPropagation()
            onDelete(e)
          }}
        >
          ×
        </button>
      ) : null}
    </div>
  )
}
