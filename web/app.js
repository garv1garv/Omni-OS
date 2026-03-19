// DOM Elements
const els = {
    status: document.getElementById('connection-status'),
    uptime: document.getElementById('uptime-badge'),
    profileBadge: document.getElementById('profile-badge'),
    btnRestart: document.getElementById('restart-btn'),
    
    // Config
    apiKey: document.getElementById('api-key'),
    btnTogglePw: document.getElementById('toggle-pw'),
    modelSelect: document.getElementById('model-select'),
    fpsSelect: document.getElementById('fps-select'),
    safeMode: document.getElementById('safe-mode'),
    failsafe: document.getElementById('failsafe'),
    btnSave: document.getElementById('save-config'),
    toast: document.getElementById('save-toast'),
    
    // Autonomy
    autonomySlider: document.getElementById('autonomy-slider'),
    autonomyValue: document.getElementById('autonomy-value'),
    autonomyHint: document.getElementById('autonomy-hint'),
    
    // Kill Switch
    killSwitch: document.getElementById('kill-switch'),
    
    // Stats
    statAudio: document.getElementById('stat-audio'),
    statScreen: document.getElementById('stat-screen'),
    statTools: document.getElementById('stat-tools'),
    statErrors: document.getElementById('stat-errors'),
    
    // Live Context
    lastTool: document.getElementById('last-tool-action'),
    aiSpeech: document.getElementById('last-ai-speech'),
    
    // Monologue
    monologueOutput: document.getElementById('monologue-output'),
    
    // Fatigue
    fatigueIcon: document.getElementById('fatigue-icon'),
    fatigueTime: document.getElementById('fatigue-time'),
    fatigueBar: document.getElementById('fatigue-bar'),
    
    // Terminal
    terminal: document.getElementById('terminal-output'),
    btnClear: document.getElementById('clear-log')
};

// State
let ws = null;
let isPwVisible = false;
let lastMonologueCount = 0;

// Format seconds into HH:MM:SS
function formatUptime(seconds) {
    const h = Math.floor(seconds / 3600).toString().padStart(2, '0');
    const m = Math.floor((seconds % 3600) / 60).toString().padStart(2, '0');
    const s = Math.floor(seconds % 60).toString().padStart(2, '0');
    return `${h}:${m}:${s}`;
}

// Append log to terminal
function appendLog(level, message, timestamp) {
    const div = document.createElement('div');
    div.className = 'log-entry';
    
    const timeSpan = document.createElement('span');
    timeSpan.className = 'log-time';
    timeSpan.textContent = `[${timestamp}]`;
    
    const msgSpan = document.createElement('span');
    msgSpan.className = `log-msg log-level-${level.toLowerCase()}`;
    msgSpan.textContent = message;
    
    div.appendChild(timeSpan);
    div.appendChild(msgSpan);
    
    els.terminal.appendChild(div);
    
    if (els.terminal.children.length > 200) {
        els.terminal.removeChild(els.terminal.firstChild);
    }
    
    els.terminal.scrollTop = els.terminal.scrollHeight;
}

// Update monologue panel
function updateMonologue(entries) {
    if (!entries || entries.length === 0) return;
    if (entries.length === lastMonologueCount) return;
    lastMonologueCount = entries.length;
    
    els.monologueOutput.innerHTML = '';
    entries.forEach(entry => {
        const div = document.createElement('div');
        div.className = 'monologue-entry';
        div.innerHTML = `<span class="mono-time">[${entry.time}]</span> ${entry.text}`;
        els.monologueOutput.appendChild(div);
    });
    els.monologueOutput.scrollTop = els.monologueOutput.scrollHeight;
}

// Update fatigue indicator
function updateFatigue(hours, threshold, exceeded) {
    const pct = Math.min(100, (hours / threshold) * 100);
    els.fatigueBar.style.width = pct + '%';
    els.fatigueTime.textContent = `${hours.toFixed(1)}h / ${threshold}h`;
    
    if (exceeded) {
        els.fatigueIcon.textContent = '🔴';
        els.fatigueBar.classList.add('fatigue-danger');
    } else if (pct > 60) {
        els.fatigueIcon.textContent = '🟡';
        els.fatigueBar.classList.remove('fatigue-danger');
    } else {
        els.fatigueIcon.textContent = '🟢';
        els.fatigueBar.classList.remove('fatigue-danger');
    }
}

// Update autonomy hint text
function updateAutonomyHint(level) {
    if (level >= 80) {
        els.autonomyHint.textContent = 'AI will execute tools autonomously';
        els.autonomyHint.style.color = '#00e676';
    } else if (level >= 40) {
        els.autonomyHint.textContent = 'AI will describe intent before executing';
        els.autonomyHint.style.color = '#ffb300';
    } else {
        els.autonomyHint.textContent = 'AI requires verbal confirmation for every action';
        els.autonomyHint.style.color = '#ff3d00';
    }
}

// Load Initial Config
async function loadConfig() {
    try {
        const res = await fetch('/api/config');
        const data = await res.json();
        
        els.apiKey.value = data.api_key;
        els.modelSelect.value = data.model;
        els.safeMode.checked = data.safe_mode;
        els.failsafe.checked = data.failsafe;
        if (data.fps) els.fpsSelect.value = data.fps.toString();
        
        appendLog('ui', 'Loaded active configuration from Omni-OS.', new Date().toLocaleTimeString());
    } catch (err) {
        appendLog('error', `Failed to load config: ${err.message}`, new Date().toLocaleTimeString());
    }
}

// Load OmniState
async function loadState() {
    try {
        const res = await fetch('/api/state');
        const data = await res.json();
        
        els.autonomySlider.value = data.autonomy;
        els.autonomyValue.textContent = data.autonomy;
        updateAutonomyHint(data.autonomy);
        els.killSwitch.checked = data.kill_switch;
        
        // Set active profile button
        document.querySelectorAll('.profile-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.profile === data.profile);
        });
        els.profileBadge.textContent = data.profile.toUpperCase();
    } catch (err) {
        appendLog('error', `Failed to load state: ${err.message}`, new Date().toLocaleTimeString());
    }
}

// Save Config
async function saveConfig() {
    els.btnSave.disabled = true;
    els.btnSave.textContent = 'Saving...';
    
    const payload = {
        api_key: els.apiKey.value,
        model: els.modelSelect.value,
        safe_mode: els.safeMode.checked,
        failsafe: els.failsafe.checked,
        fps: parseInt(els.fpsSelect.value),
    };
    
    try {
        const res = await fetch('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        
        if (res.ok) {
            els.toast.classList.add('show');
            setTimeout(() => els.toast.classList.remove('show'), 3000);
            appendLog('ui', 'Configuration updated.', new Date().toLocaleTimeString());
        }
    } catch (err) {
        alert('Failed to save config: ' + err.message);
    } finally {
        els.btnSave.disabled = false;
        els.btnSave.textContent = 'Apply Changes';
    }
}

// Post state update
async function postState(stateUpdate) {
    try {
        await fetch('/api/state', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(stateUpdate)
        });
    } catch (err) {
        appendLog('error', `Failed to update state: ${err.message}`, new Date().toLocaleTimeString());
    }
}

// Connect WebSocket
function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${window.location.host}/ws`);
    
    ws.onopen = () => {
        appendLog('ui', 'WebSocket connected. Real-time telemetry active.', new Date().toLocaleTimeString());
    };
    
    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        
        if (data.type === 'stats') {
            // Connection
            if (data.connected) {
                els.status.classList.add('connected');
            } else {
                els.status.classList.remove('connected');
            }
            
            // Badges
            els.uptime.textContent = formatUptime(data.uptime);
            els.statAudio.textContent = data.audio_chunks;
            els.statScreen.textContent = data.screen_frames;
            els.statTools.textContent = data.tool_calls;
            els.statErrors.textContent = data.tool_errors;
            
            // Last Actions
            els.lastTool.textContent = data.last_tool;
            if (data.last_speech && data.last_speech !== els.aiSpeech.dataset.last) {
                els.aiSpeech.textContent = data.last_speech;
                els.aiSpeech.dataset.last = data.last_speech;
            }
            
            // Phase 3/4 state
            if (data.profile) {
                els.profileBadge.textContent = data.profile.toUpperCase();
            }
            if (data.monologue) {
                updateMonologue(data.monologue);
            }
            if (data.fatigue_hours !== undefined) {
                updateFatigue(data.fatigue_hours, data.fatigue_threshold, data.fatigue_exceeded);
            }
            if (data.kill_switch !== undefined) {
                els.killSwitch.checked = data.kill_switch;
            }
        }
        else if (data.type === 'log') {
            appendLog(data.level, data.message, data.time);
        }
    };
    
    ws.onclose = () => {
        els.status.classList.remove('connected');
        appendLog('warn', 'WebSocket disconnected. Retrying in 3s...', new Date().toLocaleTimeString());
        setTimeout(connectWebSocket, 3000);
    };
}

// Event Listeners
els.btnTogglePw.addEventListener('click', () => {
    isPwVisible = !isPwVisible;
    els.apiKey.type = isPwVisible ? 'text' : 'password';
});

els.btnSave.addEventListener('click', saveConfig);

els.btnClear.addEventListener('click', () => {
    els.terminal.innerHTML = '';
});

// Autonomy slider
els.autonomySlider.addEventListener('input', (e) => {
    const val = parseInt(e.target.value);
    els.autonomyValue.textContent = val;
    updateAutonomyHint(val);
});
els.autonomySlider.addEventListener('change', (e) => {
    const val = parseInt(e.target.value);
    postState({ autonomy: val });
    appendLog('ui', `Autonomy set to ${val}%`, new Date().toLocaleTimeString());
});

// Profile buttons
document.querySelectorAll('.profile-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.profile-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        const profile = btn.dataset.profile;
        postState({ profile });
        els.profileBadge.textContent = profile.toUpperCase();
        appendLog('ui', `Profile switched to: ${profile}`, new Date().toLocaleTimeString());
    });
});

// Kill switch
els.killSwitch.addEventListener('change', (e) => {
    const engaged = e.target.checked;
    postState({ kill_switch: engaged });
    const statusText = engaged ? 'ENGAGED — Mic & Screen paused' : 'DISENGAGED — Streams active';
    appendLog('ui', `Kill Switch: ${statusText}`, new Date().toLocaleTimeString());
});

// Restart
els.btnRestart.addEventListener('click', async () => {
    if(!confirm("Restart the Gemini connection loop?")) return;
    try {
        await fetch('/api/restart', { method: 'POST' });
        appendLog('ui', 'Restart signal dispatched.', new Date().toLocaleTimeString());
    } catch (e) {
        console.error(e);
    }
});

// Initialization
document.addEventListener('DOMContentLoaded', () => {
    loadConfig();
    loadState();
    connectWebSocket();
});
