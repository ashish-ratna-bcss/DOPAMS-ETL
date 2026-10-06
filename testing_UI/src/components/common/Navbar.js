import React from 'react';
import { Database, Layers, RefreshCw, Sun, Moon } from 'lucide-react';

export default function Navbar({ activeTab, setActiveTab, health, onRefreshHealth, isHealthLoading, theme, onToggleTheme }) {
  return (
    <header className="glass-nav" style={{ position: 'sticky', top: 0, zIndex: 100, padding: '12px 24px' }}>
      <div style={{ maxWidth: '1600px', margin: '0 auto', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '16px' }}>
        
        {/* Brand Logo & Title */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <div style={{ 
            background: 'linear-gradient(135deg, #2563eb 0%, #7c3aed 100%)', 
            borderRadius: '10px', 
            padding: '8px', 
            display: 'flex', 
            alignItems: 'center', 
            justifyContent: 'center',
            boxShadow: '0 2px 10px rgba(124, 58, 237, 0.25)'
          }}>
            <Database size={22} color="#ffffff" />
          </div>
          <div>
            <h1 style={{ fontSize: '18px', fontWeight: 800, letterSpacing: '-0.02em', display: 'flex', alignItems: 'center', gap: '8px', color: 'var(--text-primary)' }}>
              DOPAMS CCTNS Explorer
              <span style={{ fontSize: '11px', background: 'var(--bg-elevated)', border: '1px solid var(--border-color)', padding: '2px 8px', borderRadius: '12px', color: 'var(--text-secondary)', fontWeight: 600 }}>Testing UI</span>
            </h1>
            <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
              Telangana CCTNS V1 vs V2 Schema & Complete Relational Inspector
            </p>
          </div>
        </div>

        {/* 3 Navigation Tabs */}
        <div style={{ display: 'flex', background: 'var(--bg-elevated)', padding: '4px', borderRadius: '12px', border: '1px solid var(--border-color)' }}>
          <button
            onClick={() => setActiveTab('v1')}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              padding: '8px 18px',
              borderRadius: '8px',
              fontSize: '14px',
              fontWeight: 600,
              background: activeTab === 'v1' ? 'var(--accent-v1)' : 'transparent',
              color: activeTab === 'v1' ? '#ffffff' : 'var(--text-secondary)',
              boxShadow: activeTab === 'v1' ? '0 2px 10px var(--accent-v1-glow)' : 'none',
            }}
          >
            <Layers size={16} />
            CCTNS V1 (Legacy)
            {health?.v1?.counts?.firs && (
              <span style={{ fontSize: '11px', background: activeTab === 'v1' ? 'rgba(255,255,255,0.25)' : 'var(--bg-secondary)', padding: '1px 6px', borderRadius: '10px' }}>
                {health.v1.counts.firs.toLocaleString()}
              </span>
            )}
          </button>

          <button
            onClick={() => setActiveTab('v2')}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              padding: '8px 18px',
              borderRadius: '8px',
              fontSize: '14px',
              fontWeight: 600,
              background: activeTab === 'v2' ? 'var(--accent-v2)' : 'transparent',
              color: activeTab === 'v2' ? '#ffffff' : 'var(--text-secondary)',
              boxShadow: activeTab === 'v2' ? '0 2px 10px var(--accent-v2-glow)' : 'none',
            }}
          >
            <Database size={16} />
            CCTNS V2 (Current)
            {health?.v2?.counts?.crimes && (
              <span style={{ fontSize: '11px', background: activeTab === 'v2' ? 'rgba(255,255,255,0.25)' : 'var(--bg-secondary)', padding: '1px 6px', borderRadius: '10px' }}>
                {health.v2.counts.crimes.toLocaleString()}
              </span>
            )}
          </button>
        </div>

        {/* Database Live Health Status Badges & Theme Toggle */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px', background: 'var(--bg-secondary)', padding: '6px 12px', borderRadius: '8px', border: '1px solid var(--border-color)' }}>
            <span style={{ 
              width: '8px', 
              height: '8px', 
              borderRadius: '50%', 
              background: health?.v1?.status === 'connected' ? 'var(--success)' : 'var(--danger)',
              display: 'inline-block',
              boxShadow: health?.v1?.status === 'connected' ? '0 0 8px var(--success)' : 'none'
            }} />
            <span style={{ color: 'var(--text-muted)' }}>V1:</span>
            <span style={{ fontWeight: 600, color: health?.v1?.status === 'connected' ? 'var(--text-primary)' : 'var(--danger)' }}>
              {health?.v1?.status === 'connected' ? 'Online' : 'Offline'}
            </span>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px', background: 'var(--bg-secondary)', padding: '6px 12px', borderRadius: '8px', border: '1px solid var(--border-color)' }}>
            <span style={{ 
              width: '8px', 
              height: '8px', 
              borderRadius: '50%', 
              background: health?.v2?.status === 'connected' ? 'var(--success)' : 'var(--danger)',
              display: 'inline-block',
              boxShadow: health?.v2?.status === 'connected' ? '0 0 8px var(--success)' : 'none'
            }} />
            <span style={{ color: 'var(--text-muted)' }}>V2:</span>
            <span style={{ fontWeight: 600, color: health?.v2?.status === 'connected' ? 'var(--text-primary)' : 'var(--danger)' }}>
              {health?.v2?.status === 'connected' ? 'Online' : 'Offline'}
            </span>
          </div>

          <button
            onClick={onRefreshHealth}
            title="Refresh Database Connection & Metrics"
            style={{
              background: 'var(--bg-secondary)',
              border: '1px solid var(--border-color)',
              color: 'var(--text-secondary)',
              padding: '7px 9px',
              borderRadius: '8px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <RefreshCw size={14} className={isHealthLoading ? 'animate-spin' : ''} />
          </button>

          <button
            onClick={onToggleTheme}
            title={theme === 'dark' ? 'Switch to Light Mode' : 'Switch to Dark Mode'}
            style={{
              background: 'var(--bg-secondary)',
              border: '1px solid var(--border-color)',
              color: 'var(--text-secondary)',
              padding: '7px 9px',
              borderRadius: '8px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            {theme === 'dark' ? <Sun size={15} color="#f59e0b" /> : <Moon size={15} color="#6366f1" />}
          </button>
        </div>

      </div>
    </header>
  );
}
