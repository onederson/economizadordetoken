#!/usr/bin/env python3
"""Instala ou atualiza o maestro: codigo em ~/.maestro e as skills no Claude Code, no agy e em ~/.agents.

Um comando so, para quem instala e para quem atualiza:  python instalar.py
- Se esta pasta e um clone do git, baixa a versao nova antes (git pull) e instala ela.
- Instala tudo de novo, inclusive o catalogo.toml; o catalogo antigo, se era diferente, fica salvo ao lado
  como catalogo.toml.antes-AAAAMMDD-HHMMSS.
--sem-atualizar: nao roda o git pull.  --sem-guarda: remove os hooks do guarda."""
import json, os, shutil, subprocess, sys, time

AQUI = os.path.dirname(os.path.abspath(__file__))
CASA = os.path.expanduser("~/.maestro")
# Claude Code e opencode leem ~/.claude/skills; agy le a pasta dele; Gemini CLI, Codex e outros leem ~/.agents/skills
SKILLS = [os.path.expanduser("~/.claude/skills/maestro"),
          os.path.expanduser("~/.gemini/antigravity-cli/skills/maestro"),
          os.path.expanduser("~/.agents/skills/maestro")]

# 1. Atualiza o clone (quem ja tem so roda este arquivo de novo). Se veio coisa nova, roda o instalador novo.
if "--sem-atualizar" not in sys.argv and os.environ.get("MAESTRO_SEM_ATUALIZAR") != "1"         and os.path.isdir(os.path.join(AQUI, ".git")) and shutil.which("git"):
    antes = subprocess.run(["git", "rev-parse", "HEAD"], cwd=AQUI, capture_output=True, text=True).stdout.strip()
    r = subprocess.run(["git", "pull", "--ff-only", "-q"], cwd=AQUI, capture_output=True, text=True)
    if r.returncode != 0:
        print("AVISO: nao deu para baixar a versao nova (git pull):", (r.stderr or r.stdout).strip()[:300])
        print("       instalando a versao que ja esta nesta pasta")
    else:
        depois = subprocess.run(["git", "rev-parse", "HEAD"], cwd=AQUI, capture_output=True, text=True).stdout.strip()
        if depois != antes:
            print(f"atualizado: {antes[:7]} -> {depois[:7]}")
            sys.exit(subprocess.run([sys.executable, os.path.abspath(__file__), "--sem-atualizar",
                                     *sys.argv[1:]]).returncode)
        print("ja esta na versao mais nova")

# 2. Codigo e catalogo: sempre a versao desta pasta. Catalogo antigo diferente fica salvo ao lado.
os.makedirs(CASA, exist_ok=True)
shutil.copy2(os.path.join(AQUI, "maestro.py"), CASA)
destino_cat = os.path.join(CASA, "catalogo.toml")
novo_cat = os.path.join(AQUI, "catalogo.toml")
if os.path.exists(destino_cat):
    with open(destino_cat, "rb") as a, open(novo_cat, "rb") as b:
        diferente = a.read() != b.read()
    if diferente:
        guardado = destino_cat + time.strftime(".antes-%Y%m%d-%H%M%S")
        shutil.copy2(destino_cat, guardado)
        print("catalogo antigo salvo em", guardado)
shutil.copy2(novo_cat, destino_cat)
print("codigo ->", CASA)
for pasta in SKILLS:
    if not os.path.isdir(os.path.dirname(pasta)):
        print("pulado (CLI nao encontrado):", pasta)
        continue
    os.makedirs(pasta, exist_ok=True)
    shutil.copy2(os.path.join(AQUI, "SKILL.md"), pasta)
    print("skill  ->", pasta)
    # /warroom: sala de guerra (varias frentes em paralelo pelo 'maestro.py sala')
    sala = os.path.join(os.path.dirname(pasta), "warroom")
    os.makedirs(sala, exist_ok=True)
    shutil.copy2(os.path.join(AQUI, "warroom-skill.md"), os.path.join(sala, "SKILL.md"))
    print("skill  ->", sala)
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
