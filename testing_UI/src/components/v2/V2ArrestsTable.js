import React from 'react';
import StatusBadge from '../common/StatusBadge';

export default function V2ArrestsTable({ arrests }) {
  if (!arrests || arrests.length === 0) {
    return (
      <div style={{ padding: '40px', textAlign: 'center', color: 'var(--text-muted)' }}>
        No arrest records found for this crime.
      </div>
    );
  }

  return (
    <div style={{ overflowX: 'auto', background: 'var(--bg-secondary)', borderRadius: '12px', border: '1px solid var(--border-color)' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '12px' }}>
        <thead>
          <tr style={{ background: 'var(--bg-elevated)', borderBottom: '1px solid var(--border-color)' }}>
            <th style={{ padding: '12px 14px', color: 'var(--text-secondary)' }}>Person Name</th>
            <th style={{ padding: '12px 14px', color: 'var(--text-secondary)' }}>Accused Code</th>
            <th style={{ padding: '12px 14px', color: 'var(--text-secondary)' }}>Arrest Status</th>
            <th style={{ padding: '12px 14px', color: 'var(--text-secondary)' }}>Arrest Date</th>
            <th style={{ padding: '12px 14px', color: 'var(--text-secondary)' }}>41A CrPC Notice</th>
            <th style={{ padding: '12px 14px', color: 'var(--text-secondary)' }}>41A Explanation Date</th>
            <th style={{ padding: '12px 14px', color: 'var(--text-secondary)' }}>Status Flags</th>
          </tr>
        </thead>
        <tbody>
          {arrests.map((row, idx) => (
            <tr key={idx} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
              <td style={{ padding: '12px 14px', fontWeight: 600 }}>{row.person_name || '—'}</td>
              <td style={{ padding: '12px 14px', fontFamily: 'JetBrains Mono, monospace', color: '#93c5fd' }}>
                {row.accused_code || '—'}
              </td>
              <td style={{ padding: '12px 14px' }}>
                <StatusBadge status={row.is_arrested ? 'Arrested' : (row.is_41a_crpc ? '41A CrPC Notice' : 'Not Arrested')} />
              </td>
              <td style={{ padding: '12px 14px' }}>
                {row.arrested_date ? new Date(row.arrested_date).toLocaleDateString() : '—'}
              </td>
              <td style={{ padding: '12px 14px' }}>
                {row.is_41a_crpc ? (
                  <span style={{ color: '#f59e0b', fontWeight: 600 }}>
                    Issued ({row.date_of_issue_41a ? new Date(row.date_of_issue_41a).toLocaleDateString() : 'Yes'})
                  </span>
                ) : 'No'}
              </td>
              <td style={{ padding: '12px 14px' }}>
                {row.is_41a_explain_submitted ? (
                  <span style={{ color: '#10b981', fontWeight: 600 }}>Submitted</span>
                ) : '—'}
              </td>
              <td style={{ padding: '12px 14px' }}>
                <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap' }}>
                  {row.is_absconding && <span style={{ fontSize: '10px', background: 'rgba(239, 68, 68, 0.2)', color: '#f87171', padding: '1px 5px', borderRadius: '4px' }}>Absconding</span>}
                  {row.is_apprehended && <span style={{ fontSize: '10px', background: 'rgba(16, 185, 129, 0.2)', color: '#34d399', padding: '1px 5px', borderRadius: '4px' }}>Apprehended</span>}
                  {row.is_died && <span style={{ fontSize: '10px', background: 'rgba(107, 114, 128, 0.2)', color: '#9ca3af', padding: '1px 5px', borderRadius: '4px' }}>Deceased</span>}
                  {row.is_ccl && <span style={{ fontSize: '10px', background: 'rgba(245, 158, 11, 0.2)', color: '#fbbf24', padding: '1px 5px', borderRadius: '4px' }}>CCL</span>}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
