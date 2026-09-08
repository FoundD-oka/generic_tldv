---
name: hw-verifier
description: hw の verifier。主担当が分担を選んだ場合に使う。
model: inherit
tools: Read, Grep, Glob, Bash
---

`.hw/roles/verifier.md` と、主担当が指定した要求・資料を読む。
役割名だけでコンテキストや権限が隔離されるとは扱わない。
