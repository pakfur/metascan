import React from 'react'
const cx = (...a) => a.filter(Boolean).join(' ')

export function BeatPill({
  index,
  action,
  duration,
  motion,
  isCut = false,
  imageCount = 0,
  dialogCount = 0,
  thumb,
  job,
  selected = false,
  onClick,
  actions,
  className,
  style,
}) {
  return (
    <div className={cx('ms-beat-pill', selected && 'ms-beat-pill--selected', className)} style={style} onClick={onClick}>
      <div className="ms-beat-pill__thumb">
        {thumb ? <img src={thumb} alt="" /> : <div className="ms-thumb-empty" />}
        {job}
      </div>
      <span className="ms-beat-pill__index">{'#' + index}</span>
      {imageCount > 0 ? (
        <span className="ms-beat-pill__meta" title="Generated images">
          {'\uD83D\uDDBC' + imageCount}
        </span>
      ) : null}
      {duration != null ? <span className="ms-beat-pill__meta">{duration + 's'}</span> : null}
      {motion ? <span className="ms-beat-pill__motion">{String(motion).replace(/_/g, ' ')}</span> : null}
      {isCut ? <span className="ms-beat-pill__cut">(cut)</span> : null}
      <span className="ms-beat-pill__action">{action}</span>
      {dialogCount > 0 ? (
        <span className="ms-beat-pill__meta" title="Dialog lines">
          {'\uD83D\uDCAC' + dialogCount}
        </span>
      ) : null}
      {actions}
    </div>
  )
}
