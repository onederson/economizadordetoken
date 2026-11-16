#!/usr/bin/env python3
"""Smoke test do maestro NESTA maquina: confere se o que esta instalado funciona de verdade.

  python testes/smoke.py                 rapido, gratis, sem modelo (instalacao, hooks, memoria, rota)
  python testes/smoke.py --modelo        + confirma com o Haiku que o executor roda sem o ai-memory (gasta pouco)
  python testes/smoke.py --completo      + uma tarefa real pelo maestro num repo temporario (Ollama local)
  python testes/smoke.py --inspiracoes   + compara headroom e rtk com a revisao anotada no README (rede)
  python testes/smoke.py --tudo          todos

Saida 0 = tudo OK (avisos nao reprovam); 1 = alguma FALHA.
"""
import glob, json, os, re, shutil, subprocess, sys, tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASA = os.path.expanduser("~/.maestro")
CONFIG = os.path.expanduser("~/.claude/settings.json")
SKILLS = ["~/.claude/skills/maestro", "~/.gemini/antigravity-cli/skills/maestro", "~/.agents/skills/maestro"]
PROMPT_LONGO = json.dumps({"session_id": "smoke-teste", "cwd": RAIZ,
                           "prompt": "implementa o modo economia de bateria e escreve os testes"})
falhas, avisos = [], []


def ok(msg):
    print(f"  OK     {msg}")


def falha(msg):
    falhas.append(msg)
    print(f"  FALHA  {msg}")


def aviso(msg):
    avisos.append(msg)
    print(f"  AVISO  {msg}")


def ler(caminho):
    with open(caminho, "rb") as f:
        return f.read().replace(b"\r\n", b"\n")


def rodar(cmd, **kw):
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    kw.setdefault("encoding", "utf-8")
    kw.setdefault("errors", "replace")
    return subprocess.run(cmd, **kw)


def env_limpo():
    env = dict(os.environ)
    for k in ("MAESTRO_EU", "MAESTRO_GUARDA", "MAESTRO_PROFUNDIDADE"):
        env.pop(k, None)
    return env


def instalacao():
    print("1. versao instalada == repositorio")
    pares = [("maestro.py", os.path.join(CASA, "maestro.py")), ("guarda.py", os.path.join(CASA, "guarda.py"))]
    pares += [("SKILL.md", os.path.join(os.path.expanduser(s), "SKILL.md")) for s in SKILLS
              if os.path.isdir(os.path.dirname(os.path.expanduser(s)))]
    for nome, inst in pares:
        if not os.path.exists(inst):
            falha(f"{inst} nao existe: rode python instalar.py")
        elif ler(inst) != ler(os.path.join(RAIZ, nome)):
            falha(f"{inst} difere do repositorio: rode python instalar.py (ou traga a mudanca para o repo)")
        else:
            ok(inst)
    cat = os.path.join(CASA, "catalogo.toml")
    if os.path.exists(cat) and ler(cat) != ler(os.path.join(RAIZ, "catalogo.toml")):
        aviso("catalogo.toml instalado difere do repo (o instalador preserva o seu; --catalogo sobrescreve)")


def hooks():
    print("2. guarda registrado nos hooks do Claude Code")
    try:
        with open(CONFIG, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:
        return falha(f"nao li {CONFIG}: {e}")
    esperado = {"UserPromptSubmit": "prompt", "PostToolUse": "ferramenta", "Stop": "parar"}
    comandos = {}
    for evento, modo in esperado.items():
        cmds = [h["command"] for g in cfg.get("hooks", {}).get(evento, []) for h in g.get("hooks", [])
                if "guarda.py" in h.get("command", "")]
        if len(cmds) != 1:
            falha(f"{evento}: {len(cmds)} hooks do guarda (esperado 1): rode python instalar.py")
        elif not cmds[0].rstrip().endswith(" " + modo):
            falha(f"{evento}: hook do guarda com modo errado: {cmds[0]}")
        else:
            ok(f"{evento} -> guarda {modo}")
            comandos[evento] = cmds[0]
    if "UserPromptSubmit" in comandos:
        r = rodar(comandos["UserPromptSubmit"], shell=True, input=PROMPT_LONGO, env=env_limpo(), timeout=30)
        if r.returncode == 0 and "additionalContext" in r.stdout:
            ok("o comando do hook roda e devolve o criterio")
        else:
            falha(f"o comando do hook nao respondeu (rc={r.returncode}): {(r.stderr or r.stdout)[:200]}")
    estado = os.path.join(CASA, "guarda", "smoke-teste.json")
    if os.path.exists(estado):
        os.remove(estado)


def memoria():
    print("3. memoria sem a instrucao falsa 'nao use o maestro'")
    base = os.path.expanduser("~/AppData/Local/ai-memory/wiki") if os.name == "nt" else os.path.expanduser("~/.local/share/ai-memory/wiki")
    if not os.path.isdir(base):
        return aviso(f"ai-memory nao encontrado em {base}; pulado")
    ruins = []
    for p in glob.glob(os.path.join(base, "**", "profile", "**", "*.md"), recursive=True):
        try:
            with open(p, encoding="utf-8", errors="ignore") as f:
                if re.search(r"nao use a skill maestro|nem chame maestro\.py", f.read(), re.I):
                    ruins.append(p)
        except OSError:
            pass
    if ruins:
        falha(f"{len(ruins)} pagina(s) de perfil mandando nao usar o maestro: {ruins[0]}")
    else:
        ok("nenhuma pagina de perfil contra o maestro")
    sys.path.insert(0, CASA)
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("maestro_smoke", os.path.join(CASA, "maestro.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        with open(m.config_executor(), encoding="utf-8") as f:
            plugins = json.load(f).get("enabledPlugins", {})
        if plugins.get("ai-memory@ai-memory") is False and not any(plugins.values()):
            ok("executor claude roda com todos os plugins desligados")
        else:
            falha(f"settings do executor ainda liga plugins: {plugins}")
    except Exception as e:
        falha(f"config_executor falhou: {e}")


def rota():
    print("4. maestro responde")
    for args in (["catalogo"], ["rota", "--eu", "4", "--tipo", "implementar"]):
        r = rodar([sys.executable, os.path.join(CASA, "maestro.py"), *args], env=env_limpo(), timeout=60)
        if r.returncode == 0:
            ok("maestro " + " ".join(args))
        else:
            falha(f"maestro {' '.join(args)} saiu com {r.returncode}: {(r.stderr or r.stdout)[-200:]}")
    try:
        import urllib.request
        urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=2)
        ok("Ollama local respondendo")
    except Exception:
        aviso("Ollama local nao respondeu agora (o maestro tenta abrir sozinho na hora do run)")


def modelo():
    print("5. executor claude nao enxerga o ai-memory (chamada real ao Haiku)")
    exe = shutil.which("claude")
    if not exe:
        return aviso("claude nao esta no PATH; pulado")
    import importlib.util
    spec = importlib.util.spec_from_file_location("maestro_smoke2", os.path.join(CASA, "maestro.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    env = env_limpo()
    for k in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT"):
        env.pop(k, None)
    q = 'Responda apenas SIM ou NAO: seu contexto contem a palavra "ai-memory" ou a frase "your usual choices"?'
    with tempfile.TemporaryDirectory() as d:
        r = rodar([exe, "-p", q, "--model", "haiku", "--strict-mcp-config", "--disable-slash-commands",
                   "--settings", m.config_executor()], cwd=d, env=env, timeout=180)
    resp = (r.stdout or "").strip().upper()
    if r.returncode == 0 and resp.startswith(("NAO", "NÃO")):
        ok("executor respondeu NAO: sem ai-memory")
    else:
        falha(f"executor ainda ve a memoria ou falhou (rc={r.returncode}): {resp[:120] or r.stderr[:120]}")


def completo():
    print("6. tarefa real de ponta a ponta (maestro run -> verificacao -> descartar)")
    d = tempfile.mkdtemp(prefix="maestro-smoke-")
    try:
        proj = os.path.join(d, "proj")
        os.makedirs(proj)
        with open(os.path.join(proj, "calc.py"), "w", encoding="utf-8") as f:
            f.write("def soma(a, b):\n    return a + b\n")
        for c in (["init", "-q"], ["add", "."], ["-c", "user.email=s@s", "-c", "user.name=s", "commit", "-qm", "init"]):
            rodar(["git", *c], cwd=proj)
        tarefa = os.path.join(d, "tarefa.md")
        with open(tarefa, "w", encoding="utf-8") as f:
            f.write("Em calc.py adicione `dobro(n)` que devolve n * 2. Crie teste_smoke.py que faca "
                    "`from calc import dobro; assert dobro(3) == 6; print('ok')`. Nao altere soma.\n")
        r = rodar([sys.executable, os.path.join(CASA, "maestro.py"), "run", "--eu", "4", "--tipo", "pequena",
                   "--dir", proj, "--tarefa", tarefa, "--verificar", "python teste_smoke.py"],
                  env=env_limpo(), timeout=1500)
        job = re.search(r"job (\S+)", r.stdout or "")
        if r.returncode == 0 and "verificacao: ok" in (r.stdout or "").lower():
            ok(f"executor fez a tarefa e a verificacao passou ({(job.group(1) if job else '?')})")
        elif r.returncode == 0:
            ok("run terminou com saida 0")
            aviso("nao achei 'verificacao: ok' no resumo; confira: " + (r.stdout or "")[-300:])
        else:
            falha(f"run saiu com {r.returncode}: {(r.stdout or r.stderr)[-400:]}")
        if job:
            rodar([sys.executable, os.path.join(CASA, "maestro.py"), "descartar", job.group(1)], env=env_limpo())
    finally:
        shutil.rmtree(d, ignore_errors=True)


def inspiracoes():
    print("7. inspiracoes (headroom e rtk) atualizadas desde a ultima revisao do README")
    with open(os.path.join(RAIZ, "README.md"), encoding="utf-8") as f:
        readme = f.read()
    m = re.search(r"Revisado em (\S+) \(headroom no commit `(\w+)`, rtk ([\d.]+)\)", readme)
    if not m:
        return falha("README sem a linha 'Revisado em ... (headroom no commit `x`, rtk N)'")
    data, commit, versao = m.groups()
    r = rodar(["git", "ls-remote", "https://github.com/headroomlabs-ai/headroom.git", "HEAD"], timeout=60)
    atual = (r.stdout.split() or ["?"])[0]
    if atual.startswith(commit):
        ok(f"headroom sem commits novos desde {data} ({commit})")
    else:
        aviso(f"headroom andou: revisado {commit}, agora {atual[:8]}. Revise as mudancas e atualize o README")
    r = rodar(["git", "ls-remote", "--tags", "https://github.com/rtk-ai/rtk.git"], timeout=60)
    tags = re.findall(r"refs/tags/v?(\d+\.\d+\.\d+)$", r.stdout or "", re.M)
    if not tags:
        return aviso("nao consegui listar as versoes do rtk")
    ultima = max(tags, key=lambda v: tuple(map(int, v.split("."))))
    if tuple(map(int, ultima.split("."))) > tuple(map(int, versao.split("."))):
        aviso(f"rtk tem versao estavel nova: {ultima} (revisado {versao})")
    else:
        ok(f"rtk sem versao estavel nova (ultima {ultima})")
    instalado = rodar(["rtk", "--version"]).stdout.strip() if shutil.which("rtk") else ""
    if instalado and ultima not in instalado:
        aviso(f"rtk instalado: {instalado}; ultima estavel: {ultima}")


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    a = set(sys.argv[1:])
    tudo = "--tudo" in a
    instalacao()
    hooks()
    memoria()
    rota()
    if tudo or "--modelo" in a:
        modelo()
    if tudo or "--completo" in a:
        completo()
    if tudo or "--inspiracoes" in a:
        inspiracoes()
    print(f"\n{'FALHOU' if falhas else 'PASSOU'}: {len(falhas)} falha(s), {len(avisos)} aviso(s)")
    sys.exit(1 if falhas else 0)


if __name__ == "__main__":
    main()
