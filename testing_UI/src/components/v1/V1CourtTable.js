import React from 'react';
import { FileText, ExternalLink } from 'lucide-react';
import api from '../../services/api';
import StatusBadge from '../common/StatusBadge';

export default function V1CourtTable({ courtRecords }) {
  if (!courtRecords || courtRecords.length === 0) {
    return (
      <div style={{ padding: '30px', textAlign: 'center', color: 'var(--text-muted)', fontSize: '13px' }}>
        No court disposal or chargesheet records in cctns_court for this FIR.
      </div>
    );
  }

  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '12px' }}>
        <thead>
          <tr style={{ background: 'var(--bg-elevated)', borderBottom: '1px solid var(--border-color)' }}>
            <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>Court ID</th>
            <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>Court Name</th>
            <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>Court Case No</th>
            <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>Chargesheet Date</th>
            <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>Disposal Date</th>
            <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>Disposal Type / Status</th>
            <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>Remarks</th>
            <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>Attached Document</th>
          </tr>
        </thead>
        <tbody>
          {courtRecords.map((row, idx) => {
            const hasCourtDoc = Boolean(row.dms_file_name && row.attach_path);
            const docUrl = hasCourtDoc
              ? api.getV1MediaPdfUrl(row.attach_path, row.dms_file_name, false)
              : null;

            return (
              <tr key={idx} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                <td style={{ padding: '10px 14px', fontFamily: 'JetBrains Mono, monospace', color: '#93c5fd' }}>
                  {row.court_id}
                </td>
                <td style={{ padding: '10px 14px', fontWeight: 600 }}>{row.court_name || '—'}</td>
                <td style={{ padding: '10px 14px', fontFamily: 'JetBrains Mono, monospace' }}>
                  {row.court_case_num || '—'}
                </td>
                <td style={{ padding: '10px 14px' }}>
                  {row.chargesheet_dt ? new Date(row.chargesheet_dt).toLocaleDateString() : '—'}
                </td>
                <td style={{ padding: '10px 14px' }}>
                  {row.court_disposal_dt ? new Date(row.court_disposal_dt).toLocaleDateString() : '—'}
                </td>
                <td style={{ padding: '10px 14px' }}>
                  <StatusBadge status={row.court_disposal_type || 'Pending in Court'} />
                </td>
                <td style={{ padding: '10px 14px', maxWidth: '250px', color: 'var(--text-secondary)' }}>
                  {row.court_remarks || '—'}
                </td>
                <td style={{ padding: '10px 14px' }}>
                  {hasCourtDoc ? (
                    <a
                      href={docUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      style={{
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: '4px',
                        background: 'rgba(37, 99, 235, 0.1)',
                        color: '#2563eb',
                        padding: '4px 8px',
                        borderRadius: '6px',
                        fontSize: '11px',
                        fontWeight: 600,
                        textDecoration: 'none',
                        border: '1px solid rgba(37, 99, 235, 0.2)',
                      }}
                    >
                      <FileText size={12} /> View PDF <ExternalLink size={10} />
                    </a>
                  ) : (
                    <span style={{ color: 'var(--text-muted)' }}>—</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
