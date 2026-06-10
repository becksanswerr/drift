<div align="center">

# DRIFT: Distributed Routing for Inference and Feature Tasks

**A Serverless Capability-Based Routing Framework for Decentralized AI Workloads**

[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Framework](https://img.shields.io/badge/Framework-DRIFT_v0.1-orange.svg)]()

</div>

---

## Abstract

With the exponential growth of Large Language Models (LLMs) and advanced AI pipelines, computational bottlenecks and VRAM limitations have become primary obstacles for researchers and independent developers. Current distributed computing paradigms predominantly rely on centralized servers and complex Model Parallelism algorithms. We introduce **DRIFT (Distributed Routing for Inference and Feature Tasks)**, a novel, purely peer-to-peer (P2P), serverless framework. Instead of parallelizing single models across disparate memory pools, DRIFT introduces **Super Selective Hardware Routing (SSHR)**—a highly efficient capability-based routing protocol. Devices act as autonomous nodes that bid on tasks dynamically based on their real-time hardware state. This repository contains both the theoretical foundations of DRIFT and its official open-source implementation.

---

## 1. Introduction

The conventional methods for deploying high-parameter LLMs or comprehensive AI pipelines are constrained by two primary factors:

1. **Cost & Centralization:** Reliance on monolithic cloud APIs leads to high recurring costs and data privacy concerns.
2. **The VRAM Ceiling:** Consumer hardware often lacks the requisite VRAM to load massive parameter models individually.

Existing solutions often focus on model splitting (tensor/pipeline parallelism) over network topologies, which results in severe latency overhead and requires intricate master-slave network configurations. DRIFT proposes an alternative: **Hardware Specialization via Decentralized Consensus**.

Rather than forcing every machine to run a fraction of every model, DRIFT treats hardware units as autonomous "Honest Workers" grouped into specialized departments.

## 2. The SSHR Architecture

The core innovation of the DRIFT framework is the **Super Selective Hardware Router (SSHR)**. The architecture eliminates the need for a master orchestrator node, rendering the system entirely fault-tolerant and immune to single-point-of-failure scenarios.

### 2.1 Decentralized Discovery

Nodes continuously broadcast their existence and hardware capabilities (e.g., `["llm", "tts", "vision"]`) across the local subnet using UDP broadcasting. This enables true "plug-and-play" scalability; plugging in a new GPU node immediately expands the network capacity without configuration.

### 2.2 Capability-Based Bidding Algorithm

When a client application dispatches a task to the network, the SSHR protocol initiates a localized auction:

1. **Filtering:** Nodes evaluate if their defined capabilities match the requested task.
2. **Scoring:** Eligible nodes calculate a dynamic capability score ($S_c$) based on total VRAM capacity ($V_{total}$), available RAM ($R_{avail}$), and current CPU utilization load ($\rho$):

   $$ S*c = \alpha(V*{total}) + \beta(R\_{avail}) - \gamma(\rho) $$

   _(Where $\alpha$, $\beta$, and $\gamma$ are tuning weights defined by the framework)._

3. **Consensus:** Nodes broadcast their $S_c$ over a highly constrained temporal window. The node with the absolute maximum $S_c$ achieves consensus and autonomously acquires the lock on the task.

## 3. Node Taxonomy

To facilitate modularity, DRIFT defines strict hardware typologies:

- **DNODE (Specialized Node):** A physical device dedicated to a specific subset of operations (e.g., restricted entirely to TTS generation).
- **SDNODE (Super Node):** An unrestricted wildcard node designated by the `*` capability flag, capable of competing in auctions for any incoming task type.
- **Hive:** The unified namespace representing the collective computational topology of all interconnected DNODEs.

---

## 4. Implementation & Usage

DRIFT is not purely theoretical; it is designed for immediate production and experimental deployment.

### 4.1 Environment Setup

Hardware polling (CPU/RAM/VRAM) requires the following dependencies:

```bash
pip install -r requirements.txt
```

### 4.2 Initializing the Hive

**Start a Universal Worker (SDNODE):**
This node will attempt to bid on all jobs broadcast to the network.

```bash
python drift_node.py
```

**Start a Specialized Department (DNODE):**
This node is configured strictly for Text-To-Speech execution.

```bash
python drift_node.py --caps tts
```

### 4.3 Client Interaction

The client acts strictly as a UI emitter and plays no part in the computational routing.

```bash
python drift_client.py
```

_Note: The client will emit jobs to the subnet, allowing researchers to observe the SSHR bidding and consensus mechanics in real time._

---

## 5. Conclusion & Future Work

DRIFT demonstrates that decentralized capability routing offers a highly viable alternative to monolithic cluster computing for AI inference tasks. Future iterations of the protocol will explore zero-trust encrypted job payloads, cross-subnet routing via WebRTC, and dynamic parameter offloading.

**Citation:**

> _Becksanswer (2025). DRIFT: A Serverless Capability-Based Routing Framework._
