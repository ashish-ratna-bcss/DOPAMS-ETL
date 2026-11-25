import React from 'react';
import { User } from 'lucide-react';
import StatusBadge from '../common/StatusBadge';

export default function V2PersonsAccused({ accusedList, onOpenMedia }) {
  if (!accusedList || accusedList.length === 0) {
    return (
      <div style={{ padding: '40px', textAlign: 'center', color: 'var(--text-muted)' }}>
        No accused or persons recorded for this crime.
      </div>
    );
  }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))', gap: '16px' }}>
      {accusedList.map((item, idx) => {
        const fullName = item.full_name || [item.name, item.surname].filter(Boolean).join(' ') || item.alias || `Accused #${idx + 1}`;
        const photoUrl = item.person_photo_url;
        const presentAddr = [
          item.present_house_no,
          item.present_street_road_no,
          item.present_ward_colony,
          item.present_locality_village,
          item.present_area_mandal,
          item.present_district,
          item.present_state_ut,
          item.present_pin_code,
        ].filter(Boolean).join(', ');

        const permanentAddr = [
          item.permanent_house_no,
          item.permanent_street_road_no,
          item.permanent_ward_colony,
          item.permanent_locality_village,
          item.permanent_area_mandal,
          item.permanent_district,
          item.permanent_state_ut,
          item.permanent_pin_code,
        ].filter(Boolean).join(', ');

        const traits = [
          item.build ? `Build: ${item.build}` : null,
          item.color ? `Color: ${item.color}` : null,
          item.height ? `Height: ${item.height}` : null,
          item.eyes ? `Eyes: ${item.eyes}` : null,
          item.hair ? `Hair: ${item.hair}` : null,
          item.mustache ? `Mustache: ${item.mustache}` : null,
          item.beard ? `Beard: ${item.beard}` : null,
          item.mole ? `Mole: ${item.mole}` : null,
        ].filter(Boolean).join(' • ');

        return (
          <div
            key={item.accused_id || idx}
            style={{
              background: 'var(--bg-card)',
              border: '1px solid var(--border-color)',
              borderRadius: 'var(--radius-lg)',
              padding: '16px',
              display: 'flex',
              flexDirection: 'column',
              gap: '12px',
              boxShadow: 'var(--shadow-sm)',
            }}
          >
            {/* Header with Photo & Name */}
            <div style={{ display: 'flex', gap: '14px', alignItems: 'flex-start' }}>
              {/* Profile Photo / Avatar */}
              <div
                style={{
                  width: '64px',
                  height: '64px',
                  borderRadius: '12px',
                  background: 'var(--bg-secondary)',
                  border: '1px solid var(--border-subtle)',
                  overflow: 'hidden',
                  flexShrink: 0,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  cursor: photoUrl ? 'pointer' : 'default',
                  position: 'relative',
                }}
                onClick={() => photoUrl && onOpenMedia && onOpenMedia({ file_url: photoUrl, file_name: `${fullName} Photo`, source_type: 'person' })}
              >
                {photoUrl ? (
                  <img
                    src={photoUrl}
                    alt={fullName}
                    style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                    onError={(e) => {
                      e.target.style.display = 'none';
                      e.target.parentElement.innerHTML = `<span style="font-size: 10px; color: #9ca3af;">No Photo</span>`;
                    }}
                  />
                ) : (
                  <User size={30} color="var(--text-muted)" />
                )}
              </div>

              {/* Names & Badges */}
              <div style={{ flex: 1 }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px' }}>
                  <h4 style={{ fontSize: '15px', fontWeight: 700, color: 'var(--text-primary)' }}>
                    {fullName}
                  </h4>
                  <StatusBadge status={item.accused_status || item.accused_type || 'Accused'} />
                </div>

                {item.alias && (
                  <p style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                    Alias: <strong>{item.alias}</strong>
                  </p>
                )}

                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', marginTop: '4px' }}>
                  {item.domicile_classification && (
                    <span style={{ fontSize: '10px', background: 'rgba(139, 92, 246, 0.2)', color: '#c4b5fd', padding: '1px 6px', borderRadius: '4px', fontWeight: 600 }}>
                      Domicile: {item.domicile_classification}
                    </span>
                  )}
                  {item.is_ccl && (
                    <span style={{ fontSize: '10px', background: 'rgba(239, 68, 68, 0.2)', color: '#fca5a5', padding: '1px 6px', borderRadius: '4px', fontWeight: 600 }}>
                      Juvenile (CCL)
                    </span>
                  )}
                  {item.seq_num && (
                    <span style={{ fontSize: '10px', background: 'var(--bg-elevated)', color: 'var(--text-secondary)', padding: '1px 6px', borderRadius: '4px' }}>
                      Seq: {item.seq_num}
                    </span>
                  )}
                </div>
              </div>
            </div>

            {/* Demographic Info */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px', fontSize: '12px', background: 'var(--bg-secondary)', padding: '10px', borderRadius: '8px' }}>
              <div>
                <span style={{ color: 'var(--text-muted)', display: 'block' }}>Relative / Relation:</span>
                <strong>{item.relative_name || '—'}</strong> ({item.relation_type || '—'})
              </div>
              <div>
                <span style={{ color: 'var(--text-muted)', display: 'block' }}>Age / Gender:</span>
                <strong>{item.age ? `${item.age} yrs` : '—'}</strong> / {item.gender || '—'}
              </div>
              <div>
                <span style={{ color: 'var(--text-muted)', display: 'block' }}>Mobile / Email:</span>
                <strong>{item.phone_number || '—'}</strong>
              </div>
              <div>
                <span style={{ color: 'var(--text-muted)', display: 'block' }}>Person ID:</span>
                <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: '11px', color: '#93c5fd' }}>
                  {item.person_id ? item.person_id.substring(0, 12) + '...' : '—'}
                </span>
              </div>
            </div>

            {/* Physical Traits */}
            {traits && (
              <div style={{ fontSize: '11px', color: 'var(--text-secondary)', background: 'var(--bg-elevated)', padding: '6px 10px', borderRadius: '6px' }}>
                <span>Physical Traits: </span>
                <strong>{traits}</strong>
              </div>
            )}

            {/* Present & Permanent Address */}
            <div style={{ fontSize: '11px', color: 'var(--text-secondary)', display: 'flex', flexDirection: 'column', gap: '4px' }}>
              <div>
                <strong style={{ color: 'var(--text-primary)' }}>Present: </strong>
                <span>{presentAddr || '—'}</span>
              </div>
              <div>
                <strong style={{ color: 'var(--text-primary)' }}>Permanent: </strong>
                <span>{permanentAddr || 'Same as present'}</span>
              </div>
            </div>

          </div>
        );
      })}
    </div>
  );
}
