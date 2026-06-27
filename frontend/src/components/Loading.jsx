import React from 'react'

export default function Loading({ message = 'Loading...' }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'center', padding: '40px' }}>
      <div style={{ textAlign: 'center' }}>
        <div className="spinner" aria-hidden="true" />
        <div style={{ marginTop: '12px', color: 'var(--text-dim)' }}>{message}</div>
      </div>
    </div>
  )
}
