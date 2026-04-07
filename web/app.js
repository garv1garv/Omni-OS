/**
 * OMNI-OS v4.0 — J.A.R.V.I.S. NEURAL INTELLIGENCE CONTROLLER
 * Features: Neural Brain Visualization, Waveform, Autonomous Thinking, Full HUD
 */
(function () {
    "use strict";

    const WS_URL = `ws://${location.host}/ws`;
    const API_BASE = `http://${location.host}/api`;
    const ARC_C = 2 * Math.PI * 50;

    const $ = s => document.querySelector(s);
    const $$ = s => document.querySelectorAll(s);

    const el = {
        arcReactor: $("#arc-reactor"), connDot: $("#conn-dot"), connLabel: $("#conn-label"),
        uptime: $("#uptime-badge"), profileBadge: $("#profile-badge"),
        cpuArc: $("#cpu-arc"), cpuVal: $("#cpu-val"),
        ramArc: $("#ram-arc"), ramVal: $("#ram-val"),
        diskArc: $("#disk-arc"), diskVal: $("#disk-val"),
        batArc: $("#bat-arc"), batVal: $("#bat-val"),
        netSent: $("#net-sent"), netRecv: $("#net-recv"),
        statAudio: $("#stat-audio"), statScreen: $("#stat-screen"),
        statTools: $("#stat-tools"), statErrors: $("#stat-errors"),
        aiSpeech: $("#ai-speech"), lastTool: $("#last-tool"),
        waveCanvas: $("#waveform-canvas"), brainCanvas: $("#neural-brain"),
        brainState: $("#brain-state"), brainActivity: $("#brain-activity"),
        monologue: $("#monologue-output"), logOutput: $("#log-output"), clearLog: $("#clear-log"),
        weatherContent: $("#weather-content"), newsList: $("#news-list"),
        timersList: $("#timers-list"), alertsList: $("#alerts-list"),
        autonomySlider: $("#autonomy-slider"), autonomyValue: $("#autonomy-value"),
        killSwitch: $("#kill-switch"),
        fatigueLabel: $("#fatigue-label"), fatigueBar: $("#fatigue-bar"),
        configToggle: $("#config-toggle"), configBody: $("#config-body"),
        saveConfig: $("#save-config"), togglePw: $("#toggle-pw"), apiKey: $("#api-key"),
        restartBtn: $("#restart-btn"),
    };

    let ws = null, reconnectTimer = null;
    let waveActivity = 0, brainIntensity = 0.15, brainState = "idle"; // idle, thinking, listening, speaking

    // ══════════════════════════════════════════════════════════════════════
    // NEURAL BRAIN VISUALIZATION — Living, breathing neural network
    // ══════════════════════════════════════════════════════════════════════
    class NeuralBrain {
        constructor(canvas) {
            this.canvas = canvas;
            this.ctx = canvas.getContext("2d");
            this.nodes = [];
            this.connections = [];
            this.particles = [];
            this.time = 0;
            this.resize();
            this.initNetwork();
            window.addEventListener("resize", () => this.resize());
        }

        resize() {
            const rect = this.canvas.parentElement.getBoundingClientRect();
            this.W = this.canvas.width = rect.width;
            this.H = this.canvas.height = 280;
        }

        initNetwork() {
            const count = 60;
            this.nodes = [];
            // Create nodes in a brain-like ellipse shape
            for (let i = 0; i < count; i++) {
                const angle = (i / count) * Math.PI * 2 + Math.random() * 0.5;
                const rx = this.W * 0.35 * (0.4 + Math.random() * 0.6);
                const ry = this.H * 0.35 * (0.4 + Math.random() * 0.6);
                this.nodes.push({
                    x: this.W / 2 + Math.cos(angle) * rx,
                    y: this.H / 2 + Math.sin(angle) * ry,
                    baseX: this.W / 2 + Math.cos(angle) * rx,
                    baseY: this.H / 2 + Math.sin(angle) * ry,
                    r: 1.5 + Math.random() * 2.5,
                    phase: Math.random() * Math.PI * 2,
                    speed: 0.005 + Math.random() * 0.015,
                    drift: 6 + Math.random() * 12,
                    layer: Math.floor(Math.random() * 3), // 0=input, 1=hidden, 2=output
                    activation: Math.random() * 0.3,
                    pulseTimer: Math.random() * 100,
                });
            }

            // Create connections between nearby nodes
            this.connections = [];
            for (let i = 0; i < this.nodes.length; i++) {
                for (let j = i + 1; j < this.nodes.length; j++) {
                    const dx = this.nodes[i].baseX - this.nodes[j].baseX;
                    const dy = this.nodes[i].baseY - this.nodes[j].baseY;
                    const dist = Math.sqrt(dx * dx + dy * dy);
                    if (dist < 120 && Math.random() < 0.35) {
                        this.connections.push({
                            a: i, b: j, dist,
                            signal: 0, signalDir: 1,
                            active: false,
                        });
                    }
                }
            }
        }

        spawnParticle(x, y) {
            if (this.particles.length > 40) return;
            this.particles.push({
                x, y,
                vx: (Math.random() - 0.5) * 2,
                vy: (Math.random() - 0.5) * 2,
                life: 1,
                decay: 0.01 + Math.random() * 0.02,
                r: 1 + Math.random() * 2,
            });
        }

        update(intensity, state) {
            this.time += 1;
            const I = Math.max(0.05, intensity);

            // Update nodes
            for (const n of this.nodes) {
                n.x = n.baseX + Math.sin(this.time * n.speed + n.phase) * n.drift;
                n.y = n.baseY + Math.cos(this.time * n.speed * 0.7 + n.phase) * n.drift * 0.6;

                // Neural activation
                n.pulseTimer -= 1;
                if (n.pulseTimer <= 0) {
                    n.activation = Math.min(1, n.activation + I * 0.5 + Math.random() * 0.3);
                    n.pulseTimer = 30 + Math.random() * (120 / Math.max(0.1, I));
                    if (I > 0.4 && Math.random() < I) this.spawnParticle(n.x, n.y);
                }
                n.activation *= (state === "thinking" ? 0.97 : 0.985);
            }

            // Fire signals along connections
            for (const c of this.connections) {
                if (!c.active && Math.random() < I * 0.02) {
                    c.active = true;
                    c.signal = 0;
                    c.signalDir = Math.random() > 0.5 ? 1 : -1;
                }
                if (c.active) {
                    c.signal += 0.02 + I * 0.03;
                    if (c.signal >= 1) {
                        c.active = false;
                        c.signal = 0;
                        // Activate target node
                        const target = c.signalDir > 0 ? c.b : c.a;
                        this.nodes[target].activation = Math.min(1, this.nodes[target].activation + 0.4);
                    }
                }
            }

            // Update particles
            for (let i = this.particles.length - 1; i >= 0; i--) {
                const p = this.particles[i];
                p.x += p.vx;
                p.y += p.vy;
                p.life -= p.decay;
                if (p.life <= 0) this.particles.splice(i, 1);
            }
        }

        draw(state) {
            const ctx = this.ctx;
            ctx.clearRect(0, 0, this.W, this.H);

            // Central glow
            const grd = ctx.createRadialGradient(this.W / 2, this.H / 2, 0, this.W / 2, this.H / 2, this.W * 0.35);
            const glowColor = state === "thinking" ? "99,102,241" : state === "speaking" ? "245,158,11" : "99,102,241";
            grd.addColorStop(0, `rgba(${glowColor}, ${0.02 + brainIntensity * 0.03})`);
            grd.addColorStop(1, "rgba(0,0,0,0)");
            ctx.fillStyle = grd;
            ctx.fillRect(0, 0, this.W, this.H);

            // Draw connections
            for (const c of this.connections) {
                const a = this.nodes[c.a], b = this.nodes[c.b];
                const avgActivation = (a.activation + b.activation) / 2;
                ctx.beginPath();
                ctx.moveTo(a.x, a.y);
                ctx.lineTo(b.x, b.y);
                ctx.strokeStyle = state === "thinking"
                    ? `rgba(99, 102, 241, ${0.02 + avgActivation * 0.15})`
                    : `rgba(148, 163, 184, ${0.02 + avgActivation * 0.1})`;
                ctx.lineWidth = 0.5 + avgActivation;
                ctx.stroke();

                // Draw signal traveling along connection
                if (c.active) {
                    const sx = a.x + (b.x - a.x) * (c.signalDir > 0 ? c.signal : 1 - c.signal);
                    const sy = a.y + (b.y - a.y) * (c.signalDir > 0 ? c.signal : 1 - c.signal);
                    ctx.beginPath();
                    ctx.arc(sx, sy, 2 + brainIntensity * 2, 0, Math.PI * 2);
                    ctx.fillStyle = state === "thinking"
                        ? `rgba(99, 102, 241, ${0.4 + c.signal * 0.5})`
                        : `rgba(245, 158, 11, ${0.4 + c.signal * 0.5})`;
                    ctx.fill();
                }
            }

            // Draw nodes
            for (const n of this.nodes) {
                const a = n.activation;
                const baseR = n.r * (1 + a * 0.8);
                // Outer glow
                if (a > 0.3) {
                    ctx.beginPath();
                    ctx.arc(n.x, n.y, baseR * 3, 0, Math.PI * 2);
                    const ng = state === "thinking"
                        ? `rgba(99, 102, 241, ${a * 0.08})`
                        : `rgba(148, 163, 184, ${a * 0.06})`;
                    ctx.fillStyle = ng;
                    ctx.fill();
                }
                // Core
                ctx.beginPath();
                ctx.arc(n.x, n.y, baseR, 0, Math.PI * 2);
                const coreColor = state === "thinking"
                    ? `rgba(${99 + a * 30}, ${102 + a * 60}, 241, ${0.2 + a * 0.7})`
                    : `rgba(${148 + a * 50}, ${163 + a * 40}, ${184 + a * 40}, ${0.2 + a * 0.6})`;
                ctx.fillStyle = coreColor;
                ctx.fill();
            }

            // Draw particles
            for (const p of this.particles) {
                ctx.beginPath();
                ctx.arc(p.x, p.y, p.r * p.life, 0, Math.PI * 2);
                ctx.fillStyle = `rgba(99, 102, 241, ${p.life * 0.4})`;
                ctx.fill();
            }
        }
    }

    let brain = null;

    // ══════════════════════════════════════════════════════════════════════
    // WAVEFORM VISUALIZER
    // ══════════════════════════════════════════════════════════════════════
    function drawWaveform() {
        const canvas = el.waveCanvas;
        if (!canvas) return;
        const ctx = canvas.getContext("2d");
        const W = canvas.width, H = canvas.height;
        const bars = 50;
        const bw = W / bars - 1;
        ctx.clearRect(0, 0, W, H);
        for (let i = 0; i < bars; i++) {
            const x = i * (bw + 1);
            const noise = Math.random() * 0.3;
            const base = waveActivity * (0.2 + noise);
            const center = Math.abs(i - bars / 2) / (bars / 2);
            const h = Math.max(1, base * H * (1 - center * 0.5));
            const y = (H - h) / 2;
            const grad = ctx.createLinearGradient(x, y, x, y + h);
            grad.addColorStop(0, "rgba(99,102,241,0.03)");
            grad.addColorStop(0.5, `rgba(99,102,241,${0.06 + waveActivity * 0.35})`);
            grad.addColorStop(1, "rgba(99,102,241,0.03)");
            ctx.fillStyle = grad;
            ctx.fillRect(x, y, bw, h);
        }
    }

    // ══════════════════════════════════════════════════════════════════════
    // ANIMATION LOOP — Runs brain + waveform at 30fps
    // ══════════════════════════════════════════════════════════════════════
    function animate() {
        if (brain) {
            brain.update(brainIntensity, brainState);
            brain.draw(brainState);
        }
        drawWaveform();
        requestAnimationFrame(animate);
    }

    // ══════════════════════════════════════════════════════════════════════
    // WEBSOCKET
    // ══════════════════════════════════════════════════════════════════════
    function connectWS() {
        if (ws && ws.readyState <= 1) return;
        ws = new WebSocket(WS_URL);
        ws.onopen = () => {
            el.connDot.className = "conn-dot online"; el.connLabel.textContent = "ONLINE";
            el.arcReactor.classList.add("active"); el.arcReactor.classList.remove("danger");
        };
        ws.onmessage = evt => {
            try {
                const d = JSON.parse(evt.data);
                if (d.type === "stats") handleStats(d);
                else if (d.type === "log") handleLog(d);
            } catch (e) {}
        };
        ws.onclose = () => {
            el.connDot.className = "conn-dot offline"; el.connLabel.textContent = "OFFLINE";
            el.arcReactor.classList.remove("active"); el.arcReactor.classList.add("danger");
            clearTimeout(reconnectTimer);
            reconnectTimer = setTimeout(connectWS, 2000);
        };
        ws.onerror = () => ws.close();
    }

    // ══════════════════════════════════════════════════════════════════════
    // STATS HANDLER — Updates every widget
    // ══════════════════════════════════════════════════════════════════════
    function handleStats(d) {
        if (d.connected) {
            el.connDot.className = "conn-dot online"; el.connLabel.textContent = "ONLINE";
            el.arcReactor.classList.add("active"); el.arcReactor.classList.remove("danger");
        }
        el.uptime.textContent = fmtUp(d.uptime || 0);
        el.profileBadge.textContent = (d.profile || "jarvis").toUpperCase();
        setActiveProfile(d.profile);
        el.killSwitch.checked = d.kill_switch || false;
        el.statAudio.textContent = (d.audio_chunks || 0).toLocaleString();
        el.statScreen.textContent = (d.screen_frames || 0).toLocaleString();
        el.statTools.textContent = d.tool_calls || 0;
        el.statErrors.textContent = d.tool_errors || 0;

        // AI Speech + Brain state
        if (d.last_speech && d.last_speech !== el.aiSpeech.textContent) {
            typewrite(el.aiSpeech, d.last_speech);
            waveActivity = 1;
            brainIntensity = 0.8;
            brainState = "speaking";
            el.brainState.textContent = "SPEAKING";
            el.brainState.className = "brain-state listening";
            setTimeout(() => {
                waveActivity = 0; brainIntensity = 0.15; brainState = "idle";
                el.brainState.textContent = "IDLE";
                el.brainState.className = "brain-state";
            }, 3000);
        }

        // Thinking indicator
        if (d.is_thinking) {
            brainIntensity = 0.6; brainState = "thinking";
            el.brainState.textContent = "THINKING";
            el.brainState.className = "brain-state thinking";
        } else if (d.is_listening) {
            brainIntensity = 0.35; brainState = "listening";
            el.brainState.textContent = "LISTENING";
            el.brainState.className = "brain-state listening";
        }

        el.brainActivity.textContent = `Neural Activity: ${Math.round(brainIntensity * 100)}%`;
        el.lastTool.textContent = d.last_tool || "—";
        el.autonomySlider.value = d.autonomy || 80;
        el.autonomyValue.textContent = (d.autonomy || 80) + "%";

        // Fatigue
        const fh = d.fatigue_hours || 0, ft = d.fatigue_threshold || 4;
        el.fatigueLabel.textContent = `${fh.toFixed(1)}h`;
        const pct = Math.min((fh / ft) * 100, 100);
        el.fatigueBar.style.width = pct + "%";
        el.fatigueBar.style.background = pct > 80 ? "var(--rose)" : pct > 50 ? "var(--amber)" : "var(--emerald)";

        // Vitals
        if (d.vitals) {
            setArc(el.cpuArc, el.cpuVal, d.vitals.cpu || 0);
            setArc(el.ramArc, el.ramVal, d.vitals.ram || 0);
            setArc(el.diskArc, el.diskVal, d.vitals.disk || 0);
            d.vitals.battery >= 0 ? setArc(el.batArc, el.batVal, d.vitals.battery) : (el.batVal.textContent = "AC");
            el.netSent.textContent = (d.vitals.net_sent || 0).toLocaleString();
            el.netRecv.textContent = (d.vitals.net_recv || 0).toLocaleString();
        }

        // Monologue
        if (d.monologue && d.monologue.length) {
            el.monologue.innerHTML = d.monologue.map(m =>
                `<div class="mono-entry"><span class="mono-time">[${m.time}]</span> ${esc(m.text)}</div>`
            ).join("");
            el.monologue.scrollTop = el.monologue.scrollHeight;
        }

        // Timers
        if (d.timers && Object.keys(d.timers).length) {
            el.timersList.innerHTML = Object.entries(d.timers).map(([, t]) =>
                `<div class="timer-item"><span class="timer-name">${esc(t.name)}</span><span class="timer-remaining">${fmtTmr(t.remaining)}</span></div>`
            ).join("");
        } else {
            el.timersList.innerHTML = '<div class="placeholder-text">No active timers</div>';
        }

        if (d.weather && d.weather.city) renderWeather(d.weather);
        if (d.news && d.news.length) renderNews(d.news);

        if (d.alerts && d.alerts.length) {
            el.alertsList.innerHTML = d.alerts.map(a => `<div class="alert-item">${esc(a.message)}</div>`).join("");
        } else {
            el.alertsList.innerHTML = '<div class="placeholder-text">All systems nominal ✓</div>';
        }

        // Autonomous brain thoughts → monologue
        if (d.brain_thoughts && d.brain_thoughts.length) {
            const time = new Date().toTimeString().slice(0, 8);
            const thoughtsHtml = d.brain_thoughts.map(t =>
                `<div class="mono-entry"><span class="mono-time">[${time}]</span> <span style="color:var(--purple)">🧠</span> ${esc(t)}</div>`
            ).join("");
            const suggestionsHtml = (d.suggestions || []).map(s =>
                `<div class="mono-entry"><span class="mono-time">[${time}]</span> <span style="color:var(--neon-green)">💡</span> ${esc(s)}</div>`
            ).join("");
            // Only update if there's new content
            const combined = thoughtsHtml + suggestionsHtml;
            if (combined && el.monologue._lastThought !== combined) {
                el.monologue.innerHTML += combined;
                el.monologue._lastThought = combined;
                // Trim old entries
                while (el.monologue.children.length > 30) el.monologue.removeChild(el.monologue.firstChild);
                el.monologue.scrollTop = el.monologue.scrollHeight;
            }
        }
    }

    function setArc(arc, val, pct) {
        const p = Math.max(0, Math.min(100, pct));
        arc.style.strokeDashoffset = ARC_C * (1 - p / 100);
        val.textContent = Math.round(p);
        arc.style.stroke = p > 90 ? "var(--rose)" : p > 70 ? "var(--amber)" : "var(--accent)";
    }

    function typewrite(el, text) {
        el.textContent = ""; let i = 0;
        (function t() { if (i < text.length) { el.textContent += text.charAt(i++); setTimeout(t, 12); } })();
    }

    function renderWeather(w) {
        el.weatherContent.innerHTML = `
            <div class="weather-main"><span class="weather-temp">${w.temp_c || '--'}°</span>
            <div><div class="weather-desc">${esc(w.description || '')}</div>
            <div style="font-size:.55rem;color:var(--text-dim)">${esc(w.city || '')}</div></div></div>
            <div class="weather-details">
            <span>💧 ${w.humidity || '--'}%</span><span>💨 ${w.wind_kmph || '--'} km/h</span>
            <span>☀ UV ${w.uv_index || '--'}</span><span>🌡 ${w.feels_like_c || '--'}°</span></div>`;
    }

    function renderNews(news) {
        el.newsList.innerHTML = news.slice(0, 5).map(n =>
            `<div class="news-item">${esc(n.title)}<div class="news-source">${esc(n.source || '')}</div></div>`
        ).join("");
    }

    function handleLog(d) {
        const e = document.createElement("div");
        e.className = `log-entry ${d.level || "INFO"}`;
        e.textContent = `[${d.time || '--'}] ${d.message || ''}`;
        el.logOutput.appendChild(e);
        while (el.logOutput.children.length > 80) el.logOutput.removeChild(el.logOutput.firstChild);
        el.logOutput.scrollTop = el.logOutput.scrollHeight;
    }

    // Helpers
    function fmtUp(s) { return `${p2(s / 3600 | 0)}:${p2((s % 3600) / 60 | 0)}:${p2(s % 60 | 0)}`; }
    function fmtTmr(s) { return `${p2(s / 60 | 0)}:${p2(s % 60 | 0)}`; }
    function p2(n) { return String(n).padStart(2, "0"); }
    function esc(s) { const d = document.createElement("div"); d.textContent = s; return d.innerHTML; }
    function setActiveProfile(p) { $$(".profile-btn").forEach(b => b.classList.toggle("active", b.dataset.profile === p)); }

    async function postAPI(ep, data) {
        try { await fetch(`${API_BASE}${ep}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) }); } catch (e) {}
    }

    // ══════════════════════════════════════════════════════════════════════
    // EVENTS
    // ══════════════════════════════════════════════════════════════════════
    function initEvents() {
        el.autonomySlider.addEventListener("input", () => el.autonomyValue.textContent = el.autonomySlider.value + "%");
        el.autonomySlider.addEventListener("change", () => postAPI("/state", { autonomy: +el.autonomySlider.value }));

        $$(".profile-btn").forEach(b => b.addEventListener("click", () => postAPI("/state", { profile: b.dataset.profile })));
        el.killSwitch.addEventListener("change", () => postAPI("/state", { kill_switch: el.killSwitch.checked }));

        $$(".protocol-btn").forEach(b => b.addEventListener("click", () => {
            if (ws && ws.readyState === 1) {
                ws.send(JSON.stringify({ action: "execute_protocol", protocol: b.dataset.protocol }));
                b.style.borderColor = "var(--neon-green)"; b.style.color = "var(--neon-green)";
                brainIntensity = 0.7; brainState = "thinking";
                el.brainState.textContent = "EXECUTING"; el.brainState.className = "brain-state thinking";
                setTimeout(() => { b.style.borderColor = ""; b.style.color = ""; brainIntensity = 0.15; brainState = "idle"; el.brainState.textContent = "IDLE"; el.brainState.className = "brain-state"; }, 2000);
            }
        }));

        el.clearLog.addEventListener("click", () => el.logOutput.innerHTML = "");

        // Tabs
        $$(".tab-btn").forEach(btn => btn.addEventListener("click", () => {
            $$(".tab-btn").forEach(b => b.classList.remove("active"));
            $$(".tab-content").forEach(c => c.classList.remove("active"));
            btn.classList.add("active");
            $(`#tab-${btn.dataset.tab}`).classList.add("active");
        }));

        el.configToggle.addEventListener("click", () => {
            el.configBody.classList.toggle("hidden");
            el.configToggle.innerHTML = el.configBody.classList.contains("hidden")
                ? '<span class="title-dot"></span>CONFIG ▸' : '<span class="title-dot"></span>CONFIG ▾';
        });

        el.togglePw.addEventListener("click", () => el.apiKey.type = el.apiKey.type === "password" ? "text" : "password");

        el.saveConfig.addEventListener("click", async () => {
            const data = {};
            if (el.apiKey.value) data.api_key = el.apiKey.value;
            data.model = $("#model-select").value;
            data.fps = +$("#fps-select").value;
            data.safe_mode = $("#safe-mode").checked;
            await postAPI("/config", data);
            el.saveConfig.textContent = "APPLIED ✓";
            setTimeout(() => el.saveConfig.textContent = "APPLY", 1500);
        });

        el.restartBtn.addEventListener("click", () => location.reload());
    }

    async function loadConfig() {
        try {
            const r = await fetch(`${API_BASE}/config`);
            const d = await r.json();
            if (d.api_key) el.apiKey.placeholder = d.api_key;
            if (d.model) $("#model-select").value = d.model;
            if (d.fps) $("#fps-select").value = d.fps.toString();
            if (d.safe_mode !== undefined) $("#safe-mode").checked = d.safe_mode;
        } catch (e) {}
    }

    // ══════════════════════════════════════════════════════════════════════
    // BOOT
    // ══════════════════════════════════════════════════════════════════════
    function init() {
        brain = new NeuralBrain(el.brainCanvas);
        initEvents();
        connectWS();
        loadConfig();
        animate();
        console.log("%c OMNI-OS — Neural Intelligence v4.0 ", "background:#020a1a;color:#a855f7;font-size:14px;padding:8px;font-family:Orbitron;");
    }

    document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", init) : init();
})();
