#!/usr/bin/env python3
"""Instala o maestro: codigo em ~/.maestro e a skill no Claude Code e no agy.
Preserva catalogo.toml ja instalado (use --catalogo para sobrescrever)."""
import os, shutil, sys

AQUI = os.path.dirname(os.path.abspath(__file__))
CASA = os.path.expanduser("~/.maestro")
# Claude Code e opencode leem ~/.claude/skills; agy le a pasta dele; Gemini CLI, Codex e outros leem ~/.agents/skills
SKILLS = [os.path.expanduser("~/.claude/skills/maestro"),
          os.path.expanduser("~/.gemini/antigravity-cli/skills/maestro"),
          os.path.expanduser("~/.agents/skills/maestro")]

os.makedirs(CASA, exist_ok=True)
shutil.copy2(os.path.join(AQUI, "maestro.py"), CASA)
destino_cat = os.path.join(CASA, "catalogo.toml")
if "--catalogo" in sys.argv or not os.path.exists(destino_cat):
    shutil.copy2(os.path.join(AQUI, "catalogo.toml"), destino_cat)
print("codigo ->", CASA)
for pasta in SKILLS:
    if not os.path.isdir(os.path.dirname(pasta)):
        print("pulado (CLI nao encontrado):", pasta)
        continue
    os.makedirs(pasta, exist_ok=True)
    shutil.copy2(os.path.join(AQUI, "SKILL.md"), pasta)
    print("skill  ->", pasta)
