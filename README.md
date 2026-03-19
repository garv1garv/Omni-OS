# Omni-OS

**Omni-OS (Phase 3: Advanced Toolkit, Dashboard & Action Log)** is a locally-hosted, autonomous desktop daemon. It streams real-time screen frames and microphone audio to the Gemini Multimodal Live API, plays back the AI's spoken responses, and uses native tool calling to execute OS-level commands (such as mouse movements, keyboard typing, scrolling, dragging, taking screenshots, and running terminal commands). 

It also comes with a live React/Vanilla JS web dashboard for monitoring the agent's actions, telemetry, and controlling its current context profile.

## 🌟 Key Features

* **Real-time Multimodal Interaction**: Directly hooks into your microphone and screen (1 FPS capture) to continuously stream context to Gemini.
* **Native OS Tool Calling**: The agent can autonomously interact with your desktop using `pyautogui` (click, type, navigate, terminal).
* **Live Web Dashboard Control Center**: View the agent's stats, inner monologue, current AI speech transcript, and live event log. Adjust configurations like Autonomy Level on the fly.
* **Dynamic Context Profiles**: Easily switch the AI's behavior depending on your current needs:
  * ⚡ **Default**: General cross-referencing and helpful automations.
  * 💻 **Developer**: A specialized pair-programming mode focusing on IDEs and standard output.
  * 📚 **Study**: A strict Socratic mentor that guides without providing direct answers.
  * 🛡️ **Sentinel**: Scanning for API keys, phishing attempts, and DLP.
  * ☕ **Co-Working**: A quiet flow-state mode that only offers occasional encouraging observations.
* **Fatigue Monitor**: Tracks sessions and gently alerts if you have been sitting and working for too long.
* **Action Audit Log**: All system interactions are safely kept in an appending `omni_os_actions.jsonl` log file.

## 🛡️ Safety & Failsafes

Omni-OS runs with high system privileges to automate tasks. However, safety mechanisms are built in:
* **Failsafe**: Moving the mouse to any corner of the screen (`(0,0)`) will immediately abort any `pyautogui` action.
* **Autonomy Gate**: When autonomy is set below 80%, the agent will require your verbal confirmation before executing a tool.
* **Safe Mode**: Enabled by default to block dangerous and destructive terminal commands (`rm -rf`, `format`, `taskkill`, etc.).
* **Kill Switch**: Easily pause microphone and screen feeds via the web dashboard.

## 🛠️ Prerequisites

* Python 3.9+
* Note for Windows Users: `PyAudio` might require Visual Studio C++ Build Tools or can be installed via pre-compiled `.whl` files depending on your Python version.

## 🚀 Installation & Usage

1. **Clone the repository** (or navigate to the project directory).
2. **Install the required dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
3. **Set your Gemini API Key**:
   Set the API key as an environment variable or manually update it in the web dashboard upon launching.
   * **Windows (Command Prompt):**
     ```cmd
     set GEMINI_API_KEY=your_api_key_here
     ```
   * **Windows (PowerShell):**
     ```powershell
     $env:GEMINI_API_KEY="your_api_key_here"
     ```
   * **Mac/Linux:**
     ```bash
     export GEMINI_API_KEY=your_api_key_here
     ```
4. **Run Omni-OS**:
   ```bash
   python main.py
   ```
5. **Open the Dashboard**: The local browser will serve the web app dashboard (usually on `http://localhost:8080` unless specified otherwise by `aiohttp`).

## 🧩 Architecture

The script uses four concurrent `asyncio` tasks:
1. **`send_audio()`**: Streams microphone chunks (16kHz PCM) to Gemini.
2. **`send_screen()`**: Periodically sends compressed JPEG frames of the primary monitor to Gemini.
3. **`receive_responses()`**: Receives incoming audio to play via the speaker, outputs transcripts to the web dashboard, and intercepts OS tool calls.
4. **`run_web_server()`**: Manages the `aiohttp` web server backend and websocket telemetry for the dashboard.
