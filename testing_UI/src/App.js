import React, { useState, useEffect } from 'react';
import Navbar from './components/common/Navbar';
import V1Dashboard from './components/v1/V1Dashboard';
import V2Dashboard from './components/v2/V2Dashboard';
import api from './services/api';

function App() {
  const [activeTab, setActiveTab] = useState('v1'); // 'v1' | 'v2'
  const [theme, setTheme] = useState('light'); // 'light' | 'dark'
  const [health, setHealth] = useState(null);
  const [isHealthLoading, setIsHealthLoading] = useState(false);

  // Set data-theme on document root
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
  }, [theme]);

  const toggleTheme = () => {
    setTheme(prev => (prev === 'light' ? 'dark' : 'light'));
  };

  const fetchHealth = () => {
    setIsHealthLoading(true);
    api.getHealth()
      .then((data) => {
        setHealth(data);
        setIsHealthLoading(false);
      })
      .catch((err) => {
        console.error('Failed to load DB health:', err);
        setIsHealthLoading(false);
      });
  };

  useEffect(() => {
    fetchHealth();
    // Auto refresh health stats every 60 seconds
    const interval = setInterval(fetchHealth, 60000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div style={{ minHeight: '100vh', backgroundColor: 'var(--bg-primary)', display: 'flex', flexDirection: 'column' }}>
      {/* Top Navbar with 2 Tab switcher, DB status & Theme toggle */}
      <Navbar
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        health={health}
        onRefreshHealth={fetchHealth}
        isHealthLoading={isHealthLoading}
        theme={theme}
        onToggleTheme={toggleTheme}
      />

      {/* Main Tab Content */}
      <main style={{ flex: 1 }}>
        {activeTab === 'v1' && <V1Dashboard health={health} />}
        {activeTab === 'v2' && <V2Dashboard health={health} />}
      </main>

      {/* Footer */}
      <footer style={{ borderTop: '1px solid var(--border-color)', padding: '16px 24px', textAlign: 'center', fontSize: '12px', color: 'var(--text-muted)', background: 'var(--bg-secondary)', marginTop: 'auto' }}>
        DOPAMS CCTNS Testing UI & Explorer • Live PostgreSQL Database at 192.168.103.106 • CCTNS V1 & V2 Schema Inspector
      </footer>
    </div>
  );
}

export default App;
