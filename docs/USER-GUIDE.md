# Brahma AI Evo — User & Operations Guide

Welcome to **Brahma AI Evo** (v1.1.1), your Windows-first autonomous desktop AI assistant. Brahma combines real-time Gemini Live bidirectional voice, 3D visual avatars, intelligent local & cloud tool dispatch, a modular Skills Library, and an Agent & Orchestrator Bridge into a unified companion.

---

## 1. Quick Installation & Launch

### Fresh Setup with `BrahmaEvo_Setup.exe`
1. Double-click `BrahmaEvo_Setup.exe`.
2. Follow the setup wizard to choose your install directory (defaults to `%LOCALAPPDATA%\Brahma_Evo`).
3. The installer creates a Desktop shortcut and Start Menu entry automatically.
4. Launch Brahma Evo from your desktop or start menu.

### First-Time Onboarding
On your first run:
- Brahma prompts you for your name and your Google Gemini API Key.
- You can optionally configure an OpenRouter API key for third-party models (Claude 3.5, DeepSeek, etc.).
- Your API keys are encrypted at rest using Windows DPAPI (`CryptProtectData`) — they are never stored in plaintext.

---

## 2. Choosing Your Voice & Avatar Persona

Brahma AI Evo makes voice and avatar customization effortless, even if you have no programming background.

### Gemini Live Assistant Voices
Open **Settings** (click the gear icon or press `Ctrl+,`) and navigate to **Speech Engines**:
- **Charon** *(Default)*: Deep, confident, and authoritative.
- **Puck**: Energetic, spirited, and playful.
- **Aoede**: Warm, conversational, and melodic.
- **Fenrir**: Grounded, straightforward, and direct.
- **Kore**: Soothing, calm, and gentle.

Your choice takes effect immediately for all live audio conversations.

### Local & Offline Voices
For offline or local model usage, Brahma includes:
- **Kokoro TTS**: Ultra-fast, natural sounding local speech.
- **Piper TTS**: Compact, low-latency neural TTS.
- **Windows SAPI**: Built-in Windows desktop narrator voices.

### 3D Avatar Customization
Navigate to **Avatar Instructions & Visuals** in Settings:
- **Master Switch**: Toggle 3D Avatar visual rendering on or off.
- **Visual Style**: Choose between the default 3D Holographic Orb, Live2D animated characters, or VRM 3D humanoid models.
- **Reactivity Intensity**: Adjust the slider (0 to 200%) to control how dramatically Brahma reacts to emotional tags and vocal inflection.

---

## 3. Behavior Modes & Custom Instructions

Customize Brahma's tone and personality to match your workflow:

### The 5 Behavior Presets
Under **Identity & Profile** in Settings:
1. **Professional** *(Default)*: Executive, structured, and polished. Answers are concise and organized into clear bullet points without conversational filler.
2. **Casual**: Friendly, warm, empathetic, and natural. Feels like speaking with a thoughtful colleague.
3. **Technical**: Code-first, architectural focus. Provides explicit error tracebacks, terminal commands, and deep technical context.
4. **Minimal**: Extreme brevity. Direct answers with zero conversational pleasantries or preamble.
5. **Proactive**: Anticipates subsequent tasks, suggests workflow optimizations, and surfaces risks before you ask.

### Custom Instructions (Post-Setup)
You can fine-tune Brahma's personality at any time! In the **Identity & Profile** card, use the **Custom Instructions** text box to add permanent rules, such as:
- *"Always format SQL queries in uppercase."*
- *"Remind me to take a water break every hour."*
- *"When reviewing code, highlight security concerns first."*

---

## 4. Skills Library

The **Skills Library** is Brahma's on-demand playbook engine. Unlike static system prompts, skills cost zero memory until you ask Brahma to perform a specialized task.

### Built-in Skills (15 Total)
1. **Reasoning Rigor**: Multi-perspective validation and step-by-step mathematical logic.
2. **Web Research**: Multi-source internet synthesis and fact checking.
3. **Word Count & Text Processing**: Precise linguistic and word frequency breakdown.
4. **Code Review & Security Audit**: OWASP vulnerability scanning and AST analysis.
5. **Browser Automation**: Stealth web navigation, screenshotting, and data extraction.
6. **API Mock Engine**: Instant HTTP mock endpoint simulation.
7. **Database Architect**: SQL schema migration and query optimization.
8. **Docker & DevOps**: Containerization, compose scaffolding, and health probes.
9. **Fullstack Scaffolder**: Modern web and backend application templating.
10. **Git Workflow**: Branching strategy, semantic commit formatting, and conflict resolution.
11. **GitHub Automation**: PR templates, CI workflow authoring, and issue triage.
12. **Performance Profiler**: Memory leak detection and CPU hotspot analysis.
13. **Security Auditor**: Credential scanning, permissions hardening, and red-team checks.
14. **Test-Driven Development**: Pytest and Jest test suite generation.
15. **Web Scraper (Stealth)**: Anti-bot bypass web data scraping.

You can enable or disable individual skills in the **Skills Library** card in Settings Hub.

---

## 5. Agent & Orchestrator Bridge

Brahma AI Evo can act as a conductor for your other AI tools and multi-agent systems:

1. **Local Model Servers (Tier 1)**:
   - Automatically detects local instances of **Ollama**, **LM Studio**, **vLLM**, and **text-generation-webui**.
   - Brahma can route specialized queries to local models running on your GPU.
2. **Orchestrators (Tier 2)**:
   - **Orca ADE**: Integrates with autonomous software worktrees.
   - **Paperclip**: Connects to multi-agent ticket queues and issue management.
3. **Remote & Cloud Agents (Tier 3)**:
   - Connects to remote agent servers running LangGraph, CrewAI, or AutoGen via authenticated REST endpoints.
   - Guarded by SSRF defenses, Windows DPAPI credential storage, and human approval gates for write verbs.

---

## 6. Memory & Continuous Self-Improvement

### Memory Engines & Easy GUI Setup

Brahma AI Evo provides a hybrid memory system that requires zero developer knowledge to configure. In **Settings Hub** (`Ctrl+,`), scroll to **Memory System & Connectors**:

1. **Local Storage (Default — Zero Setup)**:
   - **Recommended for most users**: 100% private, offline, and ready immediately.
   - Saves all facts and chat history to your local PC (`%LOCALAPPDATA%\BrahmaAI\memory`).
   - Click **📂 Open Memory Folder** in Settings to view or backup your memory files anytime.

2. **Mem0 Hybrid Vector Memory**:
   - Ideal for advanced semantic search across long conversations.
   - Simply select **Mem0** in the dropdown, paste your API key from [mem0.ai](https://mem0.ai), and click **💾 Save Key**.
   - Your key is encrypted with Windows DPAPI security — no terminal or environment variables needed.

3. **Letta / MemGPT Archival Memory Server**:
   - Ideal for power users running a local or cloud Letta agent memory service.
   - Enter your **Server URL** (defaults to `http://localhost:8283`), **Agent ID**, and optional **API Token**, then click **💾 Save & Connect**.

4. **Testing Your Memory**:
   - Click **🔍 Test Memory Recall** anytime to verify that Brahma can instantly store and recall memories using your selected backend. The status badge displays `🟢 Connected` with your live memory entry count.

### Where Facts are Stored
- **Semantic Facts & Preferences**: Stored locally in `%LOCALAPPDATA%\BrahmaAI\memory\long_term.json`.
- **Chat Conversations & Attachments**: Stored in `%LOCALAPPDATA%\BrahmaAI\workspace_store.db` (SQLite).

### Autonomous Self-Healing Sentry
If an action encounters a bug or exception:
1. Brahma's **Auto-Heal Sentry** captures the traceback and diagnoses the exact line.
2. An AST-validated, syntax-checked patch is synthesized in an isolated sandbox.
3. Brahma applies the patch with an automatic atomic backup.
4. You can view patch history or rollback any hotfix with one click in Settings Hub under **Auto-Heal & Self-Improvement**.

---

## 7. OpenAI-Compatible Loopback API (`brahma_api`)

Brahma AI Evo includes a built-in, local OpenAI-compatible HTTP server running on `http://127.0.0.1:8010/v1`. This allows external coding agents, editor plugins, and scripts to use Brahma as their agentic backend.

### Enabling the Port
1. Open **Settings Hub** (`Ctrl+,`) and navigate to **System Connectivity**.
2. Locate the **OpenAI-Compatible Local API (Port 8010)** card.
3. Toggle the switch to **Enabled** (or set `"enabled": true` in `config/brahma_api/config.json`).

### Minting API Tokens & Presets
To maintain strict least-privilege security, all external calls require a Bearer token with an explicit capability preset:
- **`chat`**: Pure conversational reasoning without tool or filesystem execution.
- **`reader`**: Read-only tools (`read_file`, `list_directory`, inspection).
- **`worker`**: Safe state-modifying actions contained within your project workspace.
- **`operator`**: Full capability surface (requires confirmation dialogs for sensitive actions).

In the **OpenAI-Compatible Local API** card, select your desired preset from the dropdown and click **➕ Mint New Token**. Store the token immediately, as only its SHA-256 hash is saved to disk.

### Connecting External Tools

#### 1. Cline (VS Code Extension)
- **API Provider**: `OpenAI-Compatible`
- **Base URL**: `http://127.0.0.1:8010/v1`
- **API Key**: Your minted Brahma bearer token
- **Model ID**: `brahma-evo:passthrough` (allows Cline to execute its own tools while using Brahma's routing) or `brahma-evo`

#### 2. Continue.dev (`config.json`)
```json
{
  "models": [
    {
      "title": "Brahma Evo",
      "provider": "openai",
      "model": "brahma-evo",
      "apiBase": "http://127.0.0.1:8010/v1",
      "apiKey": "YOUR_MINTED_TOKEN"
    }
  ]
}
```

#### 3. Python SDK or Custom Script
```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8010/v1",
    api_key="YOUR_MINTED_TOKEN"
)

response = client.chat.completions.create(
    model="brahma-evo",
    messages=[{"role": "user", "content": "List the files in my workspace"}]
)
print(response.choices[0].message.content)
```

---

## 8. Proactive Voice Comments & Attention

By default, Brahma can make thoughtful, proactive voice observations when significant system events or tasks occur.
- **Controlling Proactive Speech**: In **Settings Hub** under **Speech Engines**, toggle **Proactive Voice Comments** on or off. When disabled, Brahma only speaks when explicitly addressed or when finishing a requested action.

---

## 9. Troubleshooting & FAQ

### Custom Endpoint Connection Issues
- **Expected URL Format**: The **Remote URL** field in Settings expects the OpenAI Base URL (e.g. `http://localhost:20128/v1` or `http://127.0.0.1:20128/v1`). Brahma automatically targets `/chat/completions`.
- **`localhost` vs `127.0.0.1` on Windows**: On Windows systems, `localhost` attempts IPv6 (`[::1]`) before falling back to IPv4. Using `http://127.0.0.1:<port>/v1` provides lower latency and avoids connection timeouts.
- **HTTP 401 or 400 Errors**: Ensure you have entered your API Key and a currently active model ID in Settings > AI Providers. If an upstream provider in a proxy/router exhausts its token quota, switch the model to an active backup.

---

## 10. Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl+,` | Open / Close Settings Hub |
| `Ctrl+Shift+X` | Emergency Agent Stop / Cancel Current Task |
| `Space` *(Hold)* | Push-to-Talk (when Push-to-Talk mode is enabled) |
| `Ctrl+Shift+W` | Toggle Floating Workspace Sidebar |
| `Click on Bubble` | Instantly reveal complete text (fast-forward streaming) |

---

*For technical specifications, security models, and developer APIs, refer to [`docs/ARCHITECTURE-SPEC.md`](file:///C:/Users/Admin/Brahma-Ai-Evo/docs/ARCHITECTURE-SPEC.md).*
