import React, { useState } from 'react';
import { User, Users, ChevronDown, ChevronUp } from 'lucide-react';

import StatusBadge from '../common/StatusBadge';

export default function V1AccusedCard({ accused, index }) {
  const [showRelatives, setShowRelatives] = useState(false);

  // Group the 60 interrogation relative columns (12 relation types x 5 fields each)
  const relativeTypes = [
    { key: 'int_father', label: 'Father' },
    { key: 'int_mother', label: 'Mother' },
    { key: 'int_wife', label: 'Wife / Spouse' },
    { key: 'int_son', label: 'Son' },
    { key: 'int_daughter', label: 'Daughter' },
    { key: 'int_brother', label: 'Brother' },
    { key: 'int_sister', label: 'Sister' },
    { key: 'int_fil', label: 'Father-in-law' },
    { key: 'int_mil', label: 'Mother-in-law' },
    { key: 'int_uncle', label: 'Uncle' },
    { key: 'int_aunt', label: 'Aunt' },
    { key: 'int_friend', label: 'Friend / Associate' },
  ];

  const populatedRelatives = relativeTypes.filter(rel => {
    return accused[`${rel.key}_name`] || 
           accused[`${rel.key}_age`] || 
           accused[`${rel.key}_occupation`] || 
           accused[`${rel.key}_address`] || 
           accused[`${rel.key}_contact_no`];
  });

  return (
    <div
      style={{
        background: 'var(--bg-card)',
        border: '1px solid var(--border-color)',
        borderRadius: 'var(--radius-lg)',
        padding: '18px',
        marginBottom: '16px',
        boxShadow: 'var(--shadow-sm)',
      }}
    >
      {/* Accused Header Card */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '12px', borderBottom: '1px solid var(--border-subtle)', paddingBottom: '14px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <div style={{
            background: 'rgba(59, 130, 246, 0.15)',
            color: 'var(--accent-v1)',
            padding: '10px',
            borderRadius: '50%',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}>
            <User size={20} />
          </div>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span style={{ fontSize: '16px', fontWeight: 700, color: 'var(--text-primary)' }}>
                {accused.accused_name || `Accused #${index + 1}`}
              </span>
              {accused.alias_name && (
                <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
                  (alias: {accused.alias_name})
                </span>
              )}
              <StatusBadge status={accused.fir_status || 'Accused'} />
            </div>
            <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '2px' }}>
              Father: {accused.father_name || 'N/A'} • Age: {accused.age ? `${accused.age} yrs` : 'N/A'} • Gender: {accused.gender || 'N/A'} • Caste: {accused.caste || 'N/A'}
            </p>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <span style={{ fontSize: '12px', background: 'var(--bg-secondary)', padding: '4px 10px', borderRadius: '6px', color: '#93c5fd' }}>
            Accused ID: {accused.accused_id}
          </span>
        </div>
      </div>

      {/* Demographics & Contact Grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '12px', margin: '14px 0' }}>
        <div style={{ fontSize: '12px' }}>
          <span style={{ color: 'var(--text-muted)', display: 'block' }}>Occupation:</span>
          <strong style={{ color: 'var(--text-primary)' }}>{accused.accused_occupation || '—'}</strong>
        </div>
        <div style={{ fontSize: '12px' }}>
          <span style={{ color: 'var(--text-muted)', display: 'block' }}>Mobile / Phone:</span>
          <strong style={{ color: 'var(--text-primary)' }}>{accused.mobile_1 || accused.telephone_residence || '—'}</strong>
        </div>
        <div style={{ fontSize: '12px' }}>
          <span style={{ color: 'var(--text-muted)', display: 'block' }}>Nationality:</span>
          <strong style={{ color: 'var(--text-primary)' }}>{accused.nationality || 'Indian'}</strong>
        </div>
        <div style={{ fontSize: '12px' }}>
          <span style={{ color: 'var(--text-muted)', display: 'block' }}>ID Proofs (Aadhaar/PAN/Voter):</span>
          <strong style={{ color: 'var(--text-primary)' }}>{accused.aadhar_card || accused.pan_card || accused.voter_card || '—'}</strong>
        </div>
      </div>

      {/* Addresses */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '12px', background: 'var(--bg-secondary)', padding: '12px', borderRadius: '8px', marginBottom: '14px' }}>
        <div>
          <span style={{ fontSize: '11px', color: 'var(--text-muted)', textTransform: 'uppercase', fontWeight: 600 }}>Present Address</span>
          <p style={{ fontSize: '12px', color: 'var(--text-primary)', marginTop: '2px' }}>
            {accused.accused_present_address || accused.area_operation || '—'}
          </p>
        </div>
        <div>
          <span style={{ fontSize: '11px', color: 'var(--text-muted)', textTransform: 'uppercase', fontWeight: 600 }}>Permanent Address</span>
          <p style={{ fontSize: '12px', color: 'var(--text-primary)', marginTop: '2px' }}>
            {accused.accused_permanent_address || 'Same as present'}
          </p>
        </div>
      </div>

      {/* Drug & Contraband Specific Details (if any recorded in V1) */}
      {(accused.drug_type || accused.drug_desc || accused.weight_gm || accused.estimated_value) && (
        <div style={{ background: 'rgba(245, 158, 11, 0.08)', border: '1px solid rgba(245, 158, 11, 0.25)', borderRadius: '8px', padding: '10px 14px', marginBottom: '14px' }}>
          <span style={{ fontSize: '11px', color: '#f59e0b', fontWeight: 700, textTransform: 'uppercase' }}>Seizure / Drug Details in V1 Dossier</span>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '16px', marginTop: '6px', fontSize: '12px' }}>
            <span><strong>Drug Type:</strong> {accused.drug_type || '—'}</span>
            <span><strong>Weight:</strong> {accused.weight_gm ? `${accused.weight_gm} gm` : '—'}</span>
            <span><strong>Est. Value:</strong> {accused.estimated_value ? `₹${accused.estimated_value}` : '—'}</span>
            <span><strong>Packets:</strong> {accused.packets_count || '—'}</span>
          </div>
        </div>
      )}

      {/* Interrogation Relatives Section (60 Unnormalized Columns) */}
      <div style={{ borderTop: '1px solid var(--border-subtle)', paddingTop: '12px' }}>
        <button
          onClick={() => setShowRelatives(!showRelatives)}
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            width: '100%',
            background: 'var(--bg-secondary)',
            padding: '10px 14px',
            borderRadius: '8px',
            color: 'var(--text-primary)',
            fontSize: '13px',
            fontWeight: 600,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Users size={16} color="var(--accent-v1)" />
            <span>Interrogation Relatives & Associates ({populatedRelatives.length} of 12 recorded)</span>
          </div>
          {showRelatives ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
        </button>

        {showRelatives && (
          <div style={{ marginTop: '12px', display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '10px' }}>
            {relativeTypes.map((rel) => {
              const name = accused[`${rel.key}_name`];
              const age = accused[`${rel.key}_age`];
              const occ = accused[`${rel.key}_occupation`];
              const addr = accused[`${rel.key}_address`];
              const contact = accused[`${rel.key}_contact_no`];

              const hasData = name || age || occ || addr || contact;

              return (
                <div
                  key={rel.key}
                  style={{
                    background: hasData ? 'var(--bg-secondary)' : 'rgba(17, 24, 39, 0.4)',
                    border: `1px solid ${hasData ? 'var(--border-color)' : 'var(--border-subtle)'}`,
                    borderRadius: '8px',
                    padding: '10px 12px',
                    opacity: hasData ? 1 : 0.5,
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '4px' }}>
                    <span style={{ fontSize: '12px', fontWeight: 700, color: hasData ? 'var(--accent-v1)' : 'var(--text-muted)' }}>
                      {rel.label}
                    </span>
                    {hasData && (
                      <span style={{ fontSize: '10px', background: 'rgba(59, 130, 246, 0.2)', padding: '1px 5px', borderRadius: '4px', color: '#60a5fa' }}>
                        Recorded
                      </span>
                    )}
                  </div>

                  {hasData ? (
                    <div style={{ fontSize: '11px', display: 'flex', flexDirection: 'column', gap: '2px' }}>
                      <p><strong>Name:</strong> {name || '—'} {age ? `(${age} yrs)` : ''}</p>
                      <p><strong>Occ:</strong> {occ || '—'}</p>
                      <p><strong>Phone:</strong> {contact || '—'}</p>
                      <p style={{ color: 'var(--text-secondary)' }}><strong>Addr:</strong> {addr || '—'}</p>
                    </div>
                  ) : (
                    <p style={{ fontSize: '11px', color: 'var(--text-muted)', fontStyle: 'italic' }}>No relative details recorded</p>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
