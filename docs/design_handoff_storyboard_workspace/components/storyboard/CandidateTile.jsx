import React from 'react'
const cx = (...a) => a.filter(Boolean).join(' ')

export function CandidateTile({ src, selected = false, title, onClick, onDoubleClick, onExpand, className, style }) {
  return (
    <button
      type="button"
      className={cx('ms-candidate', selected && 'ms-candidate--selected', className)}
      style={style}
      title={title}
      onClick={onClick}
      onDoubleClick={onDoubleClick}
    >
      <img src={src} alt="" />
      {selected ? <span className="ms-candidate__check">{'\u2713'}</span> : null}
      {onExpand ? (
        <span
          className="ms-candidate__expand"
          title="View full size"
          onClick={(e) => {
            e.stopPropagation()
            onExpand(e)
          }}
        >
          <span className="pi pi-search-plus" aria-hidden="true" />
        </span>
      ) : null}
    </button>
  )
}
