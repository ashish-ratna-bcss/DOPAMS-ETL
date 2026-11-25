import React from 'react';
import { Scale, Clock } from 'lucide-react';
import StatusBadge from '../common/StatusBadge';

export default function V2ChargesheetsView({ chargesheets, updates, onOpenMedia }) {
  const hasCS = chargesheets && chargesheets.length > 0;
  const hasUpdates = updates && updates.length > 0;

  if (!hasCS && !hasUpdates) {
    return (
      <div style={{ padding: '40px', textAlign: 'center', color: 'var(--text-muted)' }}>
        No chargesheets or update records filed for this crime.
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      
      {/* 1. Chargesheets Table */}
      {hasCS && (
        <div style={{ background: 'var(--bg-card)', borderRadius: '12px', border: '1px solid var(--border-color)', padding: '16px' }}>
          <h4 style={{ fontSize: '14px', fontWeight: 700, color: 'var(--text-primary)', marginBottom: '12px', display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Scale size={16} color="var(--accent-v2)" /> Official Chargesheets Filed ({chargesheets.length})
          </h4>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '12px' }}>
              <thead>
                <tr style={{ background: 'var(--bg-elevated)', borderBottom: '1px solid var(--border-color)' }}>
                  <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>CS Number</th>
                  <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>CS Date</th>
                  <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>Court Name</th>
                  <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>CS Type</th>
                  <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>ICJS CS No</th>
                  <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>e-Signed</th>
                  <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>Acts & Sections</th>
                </tr>
              </thead>
              <tbody>
                {chargesheets.map((cs, idx) => (
                  <tr key={cs.id || idx} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                    <td style={{ padding: '10px 14px', fontFamily: 'JetBrains Mono, monospace', fontWeight: 600, color: '#a78bfa' }}>
                      {cs.chargesheet_no || cs.charge_sheet_no || `CS-${cs.id}`}
                    </td>
                    <td style={{ padding: '10px 14px' }}>
                      {cs.chargesheet_date || cs.charge_sheet_date ? new Date(cs.chargesheet_date || cs.charge_sheet_date).toLocaleDateString() : '—'}
                    </td>
                    <td style={{ padding: '10px 14px', fontWeight: 600 }}>{cs.court_name || '—'}</td>
                    <td style={{ padding: '10px 14px' }}>{cs.chargesheet_type || cs.charge_sheet_type || 'Final Report'}</td>
                    <td style={{ padding: '10px 14px', fontFamily: 'JetBrains Mono, monospace' }}>
                      {cs.chargesheet_no_icjs || cs.charge_sheet_no_for_icjs || '—'}
                    </td>
                    <td style={{ padding: '10px 14px' }}>
                      <StatusBadge status={cs.is_esigned ? 'e-Signed' : 'Not e-Signed'} />
                    </td>
                    <td style={{ padding: '10px 14px', maxWidth: '200px' }}>
                      {Array.isArray(cs.acts_sections) ? cs.acts_sections.join(', ') : (cs.acts_sections || cs.acts_and_sections || '—')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 2. Chargesheet Updates Log */}
      {hasUpdates && (
        <div style={{ background: 'var(--bg-card)', borderRadius: '12px', border: '1px solid var(--border-color)', padding: '16px' }}>
          <h4 style={{ fontSize: '14px', fontWeight: 700, color: 'var(--text-primary)', marginBottom: '12px', display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Clock size={16} color="#34d399" /> Court Updates & Taken on File Logs ({updates.length})
          </h4>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '12px' }}>
              <thead>
                <tr style={{ background: 'var(--bg-elevated)', borderBottom: '1px solid var(--border-color)' }}>
                  <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>Update ID</th>
                  <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>CS Number</th>
                  <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>Status</th>
                  <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>Court Case No / Taken On File</th>
                </tr>
              </thead>
              <tbody>
                {updates.map((u, idx) => (
                  <tr key={u.id || idx} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                    <td style={{ padding: '10px 14px', fontFamily: 'JetBrains Mono, monospace' }}>{u.update_charge_sheet_id || u.id}</td>
                    <td style={{ padding: '10px 14px', fontWeight: 600 }}>{u.charge_sheet_no || '—'}</td>
                    <td style={{ padding: '10px 14px' }}>
                      <StatusBadge status={u.charge_sheet_status || 'Updated'} />
                    </td>
                    <td style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>
                      {u.taken_on_file_court_case_no ? `${u.taken_on_file_case_type || 'Case'}: ${u.taken_on_file_court_case_no}` : (u.taken_on_file_date ? `Date: ${new Date(u.taken_on_file_date).toLocaleDateString()}` : '—')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

    </div>
  );
}
