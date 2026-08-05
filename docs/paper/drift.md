---
title: "DRIFT: Distributed Routing for Inference and Feature Tasks"
author: "Kaan Yedikardaşlar"
version: "0.2"
date: "2025"
---

*A Serverless, Peer-to-Peer Framework for Capability-Based LLM Inference Routing across Heterogeneous Consumer Hardware*

## 1. Abstract
The widespread availability of consumer-grade GPU hardware has created an untapped reservoir of local compute capacity. Yet existing frameworks for Large Language Model (LLM) inference either require a centralized server, demand manual cluster configuration, or approach distributed execution by partitioning a single model across devices — an approach that couples hardware tightly and limits flexibility.

This paper introduces **DRIFT (Distributed Routing for Inference and Feature Tasks)**, a serverless, peer-to-peer framework that reconceptualizes distributed LLM inference through capability-based task routing. Rather than splitting a model, DRIFT treats each physical machine (DNODE) as an autonomous worker that advertises its capabilities (DCAP) to a self-organizing network (the Hive). When a job arrives, every eligible DNODE submits a scored bid based on its real-time Cognitive Budget; the node with the highest available capacity wins and executes the task locally. New nodes join by simply launching the process — zero configuration required.

---

## 2. Introduction
Modern LLM inference is dominated by two paradigms: cloud-hosted APIs and single-machine local execution. Both carry significant limitations. Cloud APIs introduce latency, ongoing cost, and data privacy concerns. Single-machine inference is constrained by the VRAM ceiling of one GPU, making large models inaccessible to most individuals.

Meanwhile, many homes, small offices, and research labs contain multiple machines with discrete GPUs that sit largely idle. The aggregate VRAM across these machines often exceeds what any single node provides, yet no practical framework exists to harness this capacity in a lightweight, serverless manner.

DRIFT addresses this gap. Its central insight is that distributed inference does not require model partitioning. If each machine runs a complete, independent model or specialized service, then a coordination layer that routes tasks to the best available node is sufficient — and architecturally simpler. This approach draws inspiration from how skilled workers in an organization self-assign to tasks they are qualified for, weighted by their current availability and Cognitive Budget.

---

## 3. DRIFT Architecture

### 3.1 Core Terminology
| Term | Full Form | Definition |
|------|-----------|------------|
| **DRIFT** | Distributed Routing for Inference and Feature Tasks | The framework and protocol as a whole. |
| **DNODE** | Distributed Node | Any physical machine participating in the Hive. Runs one or more DCaps. |
| **SDNODE** | Super Distributed Node | A DNODE with wildcard capability (`*`). Eligible for all job types. |
| **DCAP** | Distributed Capability | A capability label advertised by a DNODE (e.g., `llm`, `tts`, `memory`, `vision`). |
| **Hive** | — | The self-organizing network of all active DNODEs. |
| **SSHR** | Super Selective Hardware Router | The scoring and routing engine that selects the winning DNODE per job based on its Cognitive Budget. |

### 3.2 System Overview
DRIFT operates as a fully decentralized, flat network. There is no master node, no registry server, and no shared state store. All coordination happens through UDP broadcast messages over a local network.

A DRIFT Hive consists of:
- DNODEs running the `drift_node` process (configured with capabilities via CLI tags, e.g., `--caps tts memory`).
- Clients broadcasting jobs.
- A shared UDP broadcast channel on port `50000`.

### 3.3 Node Lifecycle
#### Discovery Phase
On startup, each DNODE immediately begins broadcasting a discovery message (Heartbeat) every 2 seconds. The message contains the node's unique identifier, its DCaps list, and its real-time hardware status (CPU, RAM, GPU usage). All other DNODEs update their local Hive map based on these broadcasts.

#### Job Arrival
When a Client broadcasts a job, it specifies the required capability (`task_type`) and an opaque payload. Every DNODE holding the matching DCAP (or SDNODEs) becomes eligible.

#### Bidding
Each eligible DNODE computes its SSHR score (see Section 4) and broadcasts a `task_bid` message. Bids are collected during a brief bidding window (3 seconds).

#### Consensus and Execution
After the bidding window closes, each DNODE independently evaluates the full set of received bids. The node with the highest score wins. Because all nodes apply the same deterministic rule (max score wins), consensus is reached without a coordinator. The winning node broadcasts an `election_result`, executes the job locally, and returns the `task_result`.

---

## 4. The SSHR Scoring Function (Cognitive Budget)

### 4.1 Design Goals
The scoring function must be fast, hardware-aware, and produce a total ordering over all bidding nodes. It utilizes the concept of a **"Cognitive Budget"**, where each node starts with a maximum capacity and subtracts points based on its current resource utilization and active parallel tasks.

### 4.2 Formula (v0.2)
The SSHR score for a generic capability task is calculated dynamically:

`Base Score = 100 - CPU_load_percent`

For LLM specific tasks (`llm_task`), the scoring incorporates VRAM architecture and Parallel Task Penalties:
1. **Model Proximity Check:**
   - If requested model is loaded in VRAM (Hot): `Base Score = 100`
   - If requested model is on Disk (Cold): `Base Score = 80`
   - If model is missing: `Base Score = 0` (Drops out of election)
2. **Partial Model Matching:** DRIFT supports cross-platform model routing. A request for `gemma4` will automatically match both `google/gemma-4-e4b` (LM Studio) and `gemma4:e4b` (Ollama), enabling seamless hybrid environments.
3. **Parallel Task Penalty:** To prevent a single powerful node from hoarding tasks and forming a bottleneck, SSHR applies a heavy penalty for parallel executions:
   `Final Score = Base Score - (Active_Tasks_Count × 20)`

This penalty ensures true **Load Balancing**. If a high-end GPU is processing a task, its subsequent bids will drop by 20%, allowing a slightly weaker but fully idle node to win the next task.

---

## 5. Comparison with Existing Systems
| Property | Petals | Exo | EdgeShard | DRIFT |
|----------|--------|-----|-----------|-------|
| **Approach** | Layer sharding | Model sharding | Dynamic placement | **Capability routing** |
| **Central server** | Required | Not required | Not required | **Not required** |
| **Zero-config join** | No | Partial | No | **Yes** |
| **Specialization**| None | None | None | **DCAP tags (e.g. tts, memory)** |
| **Hardware** | Internet volunteers | Consumer local | IoT / edge | **Any local hardware** |

---

## 6. Current Limitations and Future Work
- **Split-brain consensus:** Two nodes may simultaneously conclude they have won if bids are lost due to network conditions. A commit-and-quorum phase is planned.
- **Streaming Result Delivery:** Currently, results are sent as a single block. Streaming tokens via WebSocket/TCP is planned.
- **WAN Support:** Extending the UDP broadcast model over WAN via optional VPN overlays (Tailscale, ZeroTier) with relay-aware routing.

## 7. Conclusion
DRIFT presents a revolutionary approach to distributed AI inference: instead of complex model partitioning, it routes complete tasks to the most capable available machine based on a real-time Cognitive Budget. The framework's zero-configuration join, capability-based specialization (DCAP), and parallel-aware load balancing make local multi-machine AI infrastructure practical, efficient, and infinitely scalable for individuals and researchers.
