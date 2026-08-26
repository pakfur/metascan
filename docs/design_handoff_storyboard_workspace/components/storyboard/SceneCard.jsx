import React from 'react'
const cx = (...a) => a.filter(Boolean).join(' ')

export function SceneCard({ name, subtitle, setting, thumbs = [], active = false, actions, onClick, className, style }) {
  return (
    <div
      className={cx('ms-scene-card', active && 'ms-scene-card--active', className)}
      style={style}
      onClick={onClick}
    >
      {actions ? <div className="ms-scene-card__corner">{actions}</div> : null}
      <div className="ms-scene-card__name">{name}</div>
      <div className="ms-scene-card__subtitle">{subtitle || '\u2014'}</div>
      {setting ? (
        <div className="ms-scene-card__setting" title={setting}>
          {setting}
        </div>
      ) : null}
      <div className="ms-scene-card__thumbs">
        {thumbs.slice(0, 6).map((src, i) => (
          <div className="ms-thumb-24" key={i}>
            {src ? <img src={src} alt="" /> : <div className="ms-thumb-empty" />}
          </div>
        ))}
      </div>
    </div>
  )
}
