import React from 'react';
import { Brain, Users, Pill, DollarSign, Activity } from 'lucide-react';

export default function V2InterrogationView({ irList }) {


  if (!irList || irList.length === 0) {
    return (
      <div style={{ padding: '40px', textAlign: 'center', color: 'var(--text-muted)' }}>
        No Interrogation Reports (IR) recorded for this crime.
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      {irList.map((ir, idx) => (
        <div
          key={ir.id || idx}
          style={{
            background: 'var(--bg-card)',
            border: '1px solid var(--border-color)',
            borderRadius: 'var(--radius-lg)',
            padding: '20px',
          }}
        >
          {/* IR Header */}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid var(--border-subtle)', paddingBottom: '14px', marginBottom: '16px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
              <div style={{ background: 'rgba(139, 92, 246, 0.15)', color: 'var(--accent-v2)', padding: '10px', borderRadius: '50%' }}>
                <Brain size={20} />
              </div>
              <div>
                <h4 style={{ fontSize: '16px', fontWeight: 700, color: 'var(--text-primary)' }}>
                  Interrogation Report: {ir.subject_name || `Subject #${idx + 1}`}
                </h4>
                <p style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                  Person ID: {ir.person_id} • Crime ID: {ir.crime_id}
                </p>
              </div>
            </div>
          </div>

          {/* IR Accordion Tabs */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '14px' }}>
            
            {/* 1. Modus Operandi & Crime Commission */}
            <div style={{ background: 'var(--bg-secondary)', padding: '14px', borderRadius: '10px', border: '1px solid var(--border-subtle)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#a78bfa', fontWeight: 600, fontSize: '13px', marginBottom: '8px' }}>
                <Activity size={16} /> Modus Operandi & Commission
              </div>
              <p style={{ fontSize: '12px', color: 'var(--text-primary)', lineHeight: 1.6 }}>
                {ir.commission_of_offence || ir.modus_operandi || 'No modus operandi recorded.'}
              </p>
              {ir.regular_habits && (
                <div style={{ marginTop: '8px', fontSize: '11px', color: 'var(--text-muted)' }}>
                  <strong>Habits:</strong> {Array.isArray(ir.regular_habits) ? ir.regular_habits.join(', ') : JSON.stringify(ir.regular_habits)}
                </div>
              )}
            </div>

            {/* 2. Drug Network & Supply */}
            <div style={{ background: 'var(--bg-secondary)', padding: '14px', borderRadius: '10px', border: '1px solid var(--border-subtle)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#f59e0b', fontWeight: 600, fontSize: '13px', marginBottom: '8px' }}>
                <Pill size={16} /> Drug Supply & Consumer Network
              </div>
              <div style={{ fontSize: '12px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
                <div>
                  <span style={{ color: 'var(--text-muted)' }}>Drugs Involved: </span>
                  <strong>{Array.isArray(ir.types_of_drugs) ? ir.types_of_drugs.join(', ') : (ir.types_of_drugs || '—')}</strong>
                </div>
                <div>
                  <span style={{ color: 'var(--text-muted)' }}>Consumers / Buyers: </span>
                  <span>{Array.isArray(ir.consumer_details) ? JSON.stringify(ir.consumer_details) : (ir.consumer_details || '—')}</span>
                </div>
                {ir.dopams_links && (
                  <div style={{ marginTop: '4px', color: '#fbbf24', fontSize: '11px' }}>
                    <strong>DOPAMS Links:</strong> {JSON.stringify(ir.dopams_links)}
                  </div>
                )}
              </div>
            </div>

            {/* 3. Associates & Local Contacts */}
            <div style={{ background: 'var(--bg-secondary)', padding: '14px', borderRadius: '10px', border: '1px solid var(--border-subtle)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#60a5fa', fontWeight: 600, fontSize: '13px', marginBottom: '8px' }}>
                <Users size={16} /> Associates & Local Contacts
              </div>
              <div style={{ fontSize: '12px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
                <div>
                  <span style={{ color: 'var(--text-muted)' }}>Associates: </span>
                  <span>{Array.isArray(ir.associate_details) ? JSON.stringify(ir.associate_details) : (ir.associate_details || '—')}</span>
                </div>
                <div>
                  <span style={{ color: 'var(--text-muted)' }}>Local Contacts: </span>
                  <span>{Array.isArray(ir.local_contacts) ? JSON.stringify(ir.local_contacts) : (ir.local_contacts || '—')}</span>
                </div>
              </div>
            </div>

            {/* 4. Financial History & Shelters */}
            <div style={{ background: 'var(--bg-secondary)', padding: '14px', borderRadius: '10px', border: '1px solid var(--border-subtle)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#10b981', fontWeight: 600, fontSize: '13px', marginBottom: '8px' }}>
                <DollarSign size={16} /> Financial History & Shelters
              </div>
              <div style={{ fontSize: '12px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
                <div>
                  <span style={{ color: 'var(--text-muted)' }}>Shelters / Hideouts: </span>
                  <span>{Array.isArray(ir.shelter) ? JSON.stringify(ir.shelter) : (ir.shelter || '—')}</span>
                </div>
                <div>
                  <span style={{ color: 'var(--text-muted)' }}>Financial Profile: </span>
                  <span>{Array.isArray(ir.financial_history) ? JSON.stringify(ir.financial_history) : (ir.financial_history || '—')}</span>
                </div>
                <div>
                  <span style={{ color: 'var(--text-muted)' }}>Amount Spent: </span>
                  <span>{ir.share_of_amount_spent || '—'}</span>
                </div>
              </div>
            </div>

          </div>
        </div>
      ))}
    </div>
  );
}
