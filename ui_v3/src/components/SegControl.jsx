import React from 'react'

const ACTIVE_STYLES = {
  'active-allow': { background: 'white', color: '#15803d' },
  'active-block': { background: 'white', color: '#dc2626' },
  'active-confirm': { background: 'white', color: '#b45309' },
  'yn-yes': { background: 'white', color: '#15803d' },
  'yn-no': { background: 'white', color: '#dc2626' },
  'm-off': { background: 'white', color: '#374151' },
  'm-monitor': { background: 'white', color: '#b45309' },
  'm-enforce': { background: 'white', color: '#15803d' },
  'm-blue': { background: 'white', color: '#4f46e5' },
}

export default function SegControl({ options = [], value, onChange }) {
  return (
    <div
      style={{
        display: 'flex',
        background: '#f3f4f6',
        borderRadius: 5,
        padding: 2,
        gap: 1,
      }}
    >
      {options.map((opt) => {
        const isActive = value === opt.value
        return (
          <div
            key={opt.value}
            onClick={() => onChange(opt.value)}
            style={{
              flex: 1,
              textAlign: 'center',
              fontSize: 11,
              fontWeight: 500,
              padding: '4px 8px',
              borderRadius: 4,
              cursor: 'pointer',
              color: '#6b7280',
              userSelect: 'none',
              boxShadow: isActive ? '0 1px 2px rgba(0,0,0,0.07)' : 'none',
              ...(isActive ? (ACTIVE_STYLES[opt.activeClass] || {}) : {}),
            }}
          >
            {opt.label}
          </div>
        )
      })}
    </div>
  )
}
