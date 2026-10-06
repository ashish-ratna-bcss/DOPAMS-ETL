import React from 'react';
import StatusBadge from '../common/StatusBadge';

export default function V1AccusedDetailsTable({ details }) {
  if (!details || details.length === 0) {
    return (
      <div style={{ padding: '30px', textAlign: 'center', color: 'var(--text-muted)', fontSize: '13px' }}>
        No secondary accused records in cctns_accused_details for this FIR.
      </div>
    );
  }

  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '12px' }}>
        <thead>
          <tr style={{ background: 'var(--bg-elevated)', borderBottom: '1px solid var(--border-color)' }}>
            <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>ID / Person Code</th>
            <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>Accused Name</th>
            <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>Father Name</th>
            <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>Age / Gender</th>
            <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>Contact</th>
            <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>Arrest Status</th>
            <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>Arrest / Surrender Date</th>
            <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>Address</th>
          </tr>
        </thead>
        <tbody>
          {details.map((row, idx) => (
            <tr key={idx} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
              <td style={{ padding: '10px 14px', fontFamily: 'JetBrains Mono, monospace', color: '#93c5fd' }}>
                {row.person_code || `AD-${row.accused_id}`}
              </td>
              <td style={{ padding: '10px 14px', fontWeight: 600 }}>{row.accused_name || '—'}</td>
              <td style={{ padding: '10px 14px' }}>{row.father_name || '—'}</td>
              <td style={{ padding: '10px 14px' }}>
                {row.age ? `${row.age} yrs` : '—'} / {row.gender || '—'}
              </td>
              <td style={{ padding: '10px 14px' }}>{row.mobile_1 || '—'}</td>
              <td style={{ padding: '10px 14px' }}>
                <StatusBadge status={row.is_arrested || 'Not Specified'} />
              </td>
              <td style={{ padding: '10px 14px' }}>
                {row.arrest_surrender_dt ? new Date(row.arrest_surrender_dt).toLocaleDateString() : '—'}
              </td>
              <td style={{ padding: '10px 14px', maxWidth: '200px', color: 'var(--text-secondary)' }}>
                {row.accused_present_address || row.accused_permanent_address || '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
