# DRIFT: Serverless P2P Node Network & Framework 🚀

**DRIFT** is a fully decentralized, serverless Peer-to-Peer (P2P) task distribution and execution framework built in Python. It allows multiple machines (Windows, Linux, macOS) on the same network to automatically discover each other, broadcast tasks, and competitively "bid" for those tasks based on their real-time hardware capabilities and active strategies.

![DRIFT Architecture](https://img.shields.io/badge/Architecture-P2P_Serverless-blue) ![Platform](https://img.shields.io/badge/Platform-Cross--Platform-success)

## ✨ Core Features

- **No Central Server:** Nodes automatically discover each other using UDP Broadcasts. If a node goes offline, the network adapts instantly.
- **Dynamic Qualify Scoring (Bidding System):** When a task is broadcast, every node calculates a "Qualify Score" (0-100%). The node with the highest score wins and executes the task.
- **Modular Strategy Architecture:** Scoring logic is entirely modular. You can easily plug in custom strategies for different types of workloads.
- **First-Class Local LLM Support:** Native integration with **Ollama** and **LM Studio** for distributed AI inference.
- **Beautiful Terminal UI:** Real-time dashboards built with `rich`, tracking peers, logs, and elections without blocking user input.

---

## 🛠️ Built-in Strategies

DRIFT uses a Strategy Pattern to determine how a node scores itself when a task arrives:

1. **Default Strategy (CPU/RAM):**
   Uses `psutil` to analyze the node's real-time CPU usage and available RAM. Perfect for generic computational tasks.
2. **LLM Management Strategy (GPU/VRAM Focused):**
   Specifically designed to distribute Large Language Model (LLM) tasks.
   - Awards **100 points** if the required model is already loaded in the node's memory.
   - Penalizes the score dynamically if the node is already processing tasks in parallel.
   - Makes real API calls to local backends (Ollama or LM Studio) to generate responses.

---

## 🚀 Installation & Usage

1. Clone the repository and install requirements:

```bash
git clone https://github.com/becksanswerr/drift.git
cd drift
pip install -r requirements.txt
```

2. Run the node:

```bash
python drift_node.py
```

_Upon startup, you will be prompted to name your node and select a strategy._

### Advanced Startup (LLM Mode)

You can launch a node as a dedicated LLM worker by passing arguments via CLI. This automatically selects the LLM Strategy.

```bash
# Preload a model (Never unloads)
python drift_node.py --preload-model gemma:2b

# Specify the backend (ollama, lmstudio, mock)
python drift_node.py --preload-model gemma-4-e4b --llm-backend lmstudio
```

---

## 💻 Terminal Commands

Once the node is running, you can interact with the network using the command bar at the bottom of the UI:

- `mock` : Broadcasts a randomly generated generic task (great for testing the CPU strategy).
- `task <description>` : Broadcasts a custom generic task.
- `mockllm` : Broadcasts a random LLM generation task.
- `taskllm <model> <prompt>` : Broadcasts a specific LLM prompt to the network.
  - _Example:_ `taskllm gemma:2b Sence gökyüzü neden mavi?`
- `quit` or `exit` : Safely shuts down the node.

---

## 🤖 LLM Backend Integration Guide

DRIFT's `LLMStrategy` can directly communicate with your local AI models.

**For Ollama (Ubuntu/Linux/Windows):**

1. Pull the model you want to use: `ollama run gemma4:e4b`
2. Start DRIFT with Ollama backend (default): `python drift_node.py` -> Select Strategy 2 -> Select Ollama.

**For LM Studio (Windows/macOS):**

1. Open LM Studio and load your desired model.
2. Start the "Local Server" (↔️ icon) on port 1234.
3. Start DRIFT and select the LM Studio backend.

When a node wins an LLM task, it will automatically route the prompt to the active backend, generate the response, and log it to the network!

---

_Built with ❤️ for decentralized edge computing._
