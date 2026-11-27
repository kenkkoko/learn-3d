# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users
Kai (career-switcher heading into an AI & RPA engineering job) and a few friends. They sit at a desktop for ~30-minute focused study sessions, usually in the evening, to learn a topic (SQL, C#/ASP.NET, RAG, English/TOEIC) by one-question-at-a-time practice.

## Product Purpose
Turn any topic or free-text learning goal into a prerequisite-ordered learning path, shown as a 3D knowledge nebula, then drill it through a question → confidence → answer → AI verdict loop with spaced review, until mastery is visible.

## Positioning
The AI runs on the learner's own computer through a local bridge (Claude Code / Codex / Ollama, their own subscription), so there are no API costs; progress is a living 3D map of what you know, not a course list.

## Operating Context
Three-column workspace: topic list (left), nebula / list view of the path (center), lesson + Q&A panel (right). Local bridge status, AI settings and subscription usage live bottom-left. Code topics (SQL/Python/JS) can run answers in the browser.

## Capabilities and Constraints
Static frontend on Cloudflare (plain HTML/CSS/JS, Three.js nebula); Python bridge on 127.0.0.1. Traditional Chinese UI. Mastery states: unlearned, weak, half, mastered, current node, locked by prerequisites.

## Brand Commitments
Name "Learn 3D · 知識星雲". Nebula icon (dark tile, orange core #f0a46a, cream nodes). User-pinned for the redesign: the 3D nebula stays the hero; dark background stays the default with a light-mode toggle; liquid / transparent glass material; three-column layout.

## Product Principles
- The nebula is the map of your own knowledge; every surface serves reading it and acting on it.
- One question at a time; the Q&A panel must stay calm and legible for 30-minute sessions.
- Honest status: bridge, AI and usage state are always visible, never fake.
