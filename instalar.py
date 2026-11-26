#!/usr/bin/env python3
"""Instala o maestro: codigo em ~/.maestro e a skill no Claude Code e no agy.
Preserva catalogo.toml ja instalado (use --catalogo para sobrescrever)."""
import json, os, shutil, sys

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
    # /claudevalidateopus (esforco medio) e /claudevalidateopusmax (esforco maximo): revisao de senior pelo
    # 'maestro.py validar'. O Fable 5.2 saiu do plano Pro em 2026-11: a skill antiga dele e removida.
    with open(os.path.join(AQUI, "validar-skill.md"), encoding="utf-8") as f:
        modelo_skill = f.read()
    for modelo, nome, esforco in (("opus", "Opus 5.5", "esforco medio"), ("opusmax", "Opus 5.5", "esforco maximo")):
        destino = os.path.join(os.path.dirname(pasta), f"claudevalidate{modelo}")
        os.makedirs(destino, exist_ok=True)
        with open(os.path.join(destino, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(modelo_skill.replace("{modelo}", modelo).replace("{Nome}", nome).replace("{esforco}", esforco))
        print("skill  ->", destino)
    antiga = os.path.join(os.path.dirname(pasta), "claudevalidatefable")
    if os.path.isfile(os.path.join(antiga, "SKILL.md")):
        shutil.rmtree(antiga)
        print("skill  x", antiga, "(Fable saiu do plano)")

# Guarda: hooks do Claude Code que cobram a decisao de delegar (ver guarda.py). --sem-guarda remove.
shutil.copy2(os.path.join(AQUI, "guarda.py"), CASA)
CONFIG = os.path.expanduser("~/.claude/settings.json")
if os.path.isdir(os.path.dirname(CONFIG)):
    try:
        with open(CONFIG, encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        cfg = {}
    guarda = os.path.join(CASA, "guarda.py").replace("\\", "/")
    py = sys.executable.replace("\\", "/")
    hooks = cfg.setdefault("hooks", {})
    for evento in list(hooks):  # tira versoes antigas do guarda antes de registrar de novo
        hooks[evento] = [g for g in hooks[evento] if "guarda.py" not in json.dumps(g)]
        if not hooks[evento]:
            del hooks[evento]
    if "--sem-guarda" not in sys.argv:
        for evento, modo, matcher in (("UserPromptSubmit", "prompt", None),
                                      ("PostToolUse", "ferramenta", "*"),
                                      ("Stop", "parar", None)):
            grupo = {"hooks": [{"type": "command", "command": f'"{py}" -I "{guarda}" {modo}', "timeout": 10}]}
            if matcher:
                grupo["matcher"] = matcher
            hooks.setdefault(evento, []).append(grupo)
    if not hooks:
        cfg.pop("hooks", None)
    shutil.copy2(CONFIG, CONFIG + ".antes-do-guarda") if os.path.exists(CONFIG) else None
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    print("guarda ->", CONFIG, "(removido)" if "--sem-guarda" in sys.argv else "(hooks prompt/ferramenta/parar)")
