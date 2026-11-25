import React from 'react';
import { Package, FlaskConical, DollarSign, Image as ImageIcon } from 'lucide-react';

import StatusBadge from '../common/StatusBadge';

export default function V2SeizuresProperties({ seizures, fslList, properties, onOpenMedia }) {
  const hasSeizures = seizures && seizures.length > 0;
  const hasFsl = fslList && fslList.length > 0;
  const hasProperties = properties && properties.length > 0;

  if (!hasSeizures && !hasFsl && !hasProperties) {
    return (
      <div style={{ padding: '40px', textAlign: 'center', color: 'var(--text-muted)' }}>
        No MO seizures, FSL case properties, or recovered properties recorded for this crime.
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      
      {/* 1. MO Seizures Table */}
      {hasSeizures && (
        <div style={{ background: 'var(--bg-card)', borderRadius: '12px', border: '1px solid var(--border-color)', padding: '16px' }}>
          <h4 style={{ fontSize: '14px', fontWeight: 700, color: 'var(--text-primary)', marginBottom: '12px', display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Package size={16} color="#fbbf24" /> Material Object (MO) Seizures ({seizures.length})
          </h4>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '12px' }}>
              <thead>
                <tr style={{ background: 'var(--bg-elevated)', borderBottom: '1px solid var(--border-color)' }}>
                  <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>MO ID / Type</th>
                  <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>Sub Type & Description</th>
                  <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>Seized From / By</th>
                  <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>Seized Date</th>
                  <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>Place & GPS Coordinates</th>
                  <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>Evidence Strength</th>
                  <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>Photo</th>
                </tr>
              </thead>
              <tbody>
                {seizures.map((mo, idx) => (
                  <tr key={mo.id || idx} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                    <td style={{ padding: '10px 14px' }}>
                      <span style={{ fontWeight: 700, color: 'var(--text-primary)' }}>{mo.type || 'Object'}</span>
                      <span style={{ display: 'block', fontFamily: 'JetBrains Mono, monospace', fontSize: '11px', color: 'var(--text-muted)' }}>
                        {mo.mo_id || mo.mo_seizure_id}
                      </span>
                    </td>
                    <td style={{ padding: '10px 14px' }}>
                      <strong>{mo.sub_type || '—'}</strong>
                      <p style={{ fontSize: '11px', color: 'var(--text-secondary)', marginTop: '2px', maxWidth: '240px' }}>
                        {mo.description || '—'}
                      </p>
                    </td>
                    <td style={{ padding: '10px 14px' }}>
                      <span>From: {mo.seized_from || '—'}</span>
                      <span style={{ display: 'block', fontSize: '11px', color: 'var(--text-muted)' }}>By: {mo.seized_by || '—'}</span>
                    </td>
                    <td style={{ padding: '10px 14px' }}>
                      {mo.seized_at ? new Date(mo.seized_at).toLocaleDateString() : '—'}
                    </td>
                    <td style={{ padding: '10px 14px', fontSize: '11px' }}>
                      <span>{[mo.pos_landmark, mo.pos_city, mo.pos_district].filter(Boolean).join(', ') || '—'}</span>
                      {(mo.pos_latitude || mo.pos_longitude) && (
                        <span style={{ display: 'block', color: '#60a5fa', fontFamily: 'JetBrains Mono, monospace' }}>
                          GPS: {mo.pos_latitude}, {mo.pos_longitude}
                        </span>
                      )}
                    </td>
                    <td style={{ padding: '10px 14px' }}>
                      <StatusBadge status={mo.strength_of_evidence || 'Strong'} />
                    </td>
                    <td style={{ padding: '10px 14px' }}>
                      {mo.mo_media_url ? (
                        <button
                          onClick={() => onOpenMedia && onOpenMedia({ file_url: mo.mo_media_url, file_name: `${mo.type} Photo`, source_type: 'mo_seizures' })}
                          style={{ display: 'flex', alignItems: 'center', gap: '4px', background: 'var(--bg-secondary)', padding: '4px 8px', borderRadius: '4px', fontSize: '11px', color: '#60a5fa' }}
                        >
                          <ImageIcon size={12} /> View Photo
                        </button>
                      ) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 2. FSL Forensic Case Properties */}
      {hasFsl && (
        <div style={{ background: 'var(--bg-card)', borderRadius: '12px', border: '1px solid var(--border-color)', padding: '16px' }}>
          <h4 style={{ fontSize: '14px', fontWeight: 700, color: 'var(--text-primary)', marginBottom: '12px', display: 'flex', alignItems: 'center', gap: '8px' }}>
            <FlaskConical size={16} color="#06b6d4" /> FSL Forensic Lab Properties ({fslList.length})
          </h4>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '12px' }}>
              <thead>
                <tr style={{ background: 'var(--bg-elevated)', borderBottom: '1px solid var(--border-color)' }}>
                  <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>Case Property ID</th>
                  <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>MO ID</th>
                  <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>Status</th>
                  <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>Send Date</th>
                  <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>Case Type</th>
                </tr>
              </thead>
              <tbody>
                {fslList.map((fsl, idx) => (
                  <tr key={fsl.id || idx} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                    <td style={{ padding: '10px 14px', fontFamily: 'JetBrains Mono, monospace', color: '#67e8f9' }}>
                      {fsl.case_property_id || fsl.id}
                    </td>
                    <td style={{ padding: '10px 14px', fontFamily: 'JetBrains Mono, monospace' }}>{fsl.mo_id || '—'}</td>
                    <td style={{ padding: '10px 14px' }}>
                      <StatusBadge status={fsl.status || 'Sent to FSL'} />
                    </td>
                    <td style={{ padding: '10px 14px' }}>
                      {fsl.send_date ? new Date(fsl.send_date).toLocaleDateString() : '—'}
                    </td>
                    <td style={{ padding: '10px 14px' }}>{fsl.case_type || 'Forensic Examination'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 3. General Properties & Recovered Values */}
      {hasProperties && (
        <div style={{ background: 'var(--bg-card)', borderRadius: '12px', border: '1px solid var(--border-color)', padding: '16px' }}>
          <h4 style={{ fontSize: '14px', fontWeight: 700, color: 'var(--text-primary)', marginBottom: '12px', display: 'flex', alignItems: 'center', gap: '8px' }}>
            <DollarSign size={16} color="#34d399" /> Recovered & Seized Properties ({properties.length})
          </h4>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '12px' }}>
              <thead>
                <tr style={{ background: 'var(--bg-elevated)', borderBottom: '1px solid var(--border-color)' }}>
                  <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>Property Category</th>
                  <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>Particulars</th>
                  <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>Estimated Value</th>
                  <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>Recovered Value</th>
                  <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>Status</th>
                  <th style={{ padding: '10px 14px', color: 'var(--text-secondary)' }}>Place of Recovery</th>
                </tr>
              </thead>
              <tbody>
                {properties.map((p, idx) => (
                  <tr key={p.id || idx} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                    <td style={{ padding: '10px 14px', fontWeight: 600 }}>{p.category || 'Property'}</td>
                    <td style={{ padding: '10px 14px', maxWidth: '250px' }}>{p.particular_of_property || p.nature || '—'}</td>
                    <td style={{ padding: '10px 14px', color: '#f59e0b', fontWeight: 600 }}>
                      {p.estimate_value ? `₹${Number(p.estimate_value).toLocaleString()}` : '—'}
                    </td>
                    <td style={{ padding: '10px 14px', color: '#10b981', fontWeight: 600 }}>
                      {p.recovered_value ? `₹${Number(p.recovered_value).toLocaleString()}` : '—'}
                    </td>
                    <td style={{ padding: '10px 14px' }}>
                      <StatusBadge status={p.property_status || 'Seized'} />
                    </td>
                    <td style={{ padding: '10px 14px' }}>{p.place_of_recovery || '—'}</td>
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
