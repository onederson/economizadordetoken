#!/usr/bin/env python3
"""Guarda do maestro: hooks do Claude Code que obrigam a decidir, na hora certa, se a tarefa vai para o maestro.

A skill sozinha depende de o modelo lembrar dela. O guarda roda em todo turno, sem depender disso:
  prompt   (UserPromptSubmit) lembra o criterio e pede a decisao declarada: "maestro: sim/nao - motivo"
  ferramenta (PostToolUse)    conta o trabalho feito pelo modelo caro desde a ultima delegacao; ao passar
                              do limite, cobra uma reavaliacao (e repete a cada novo bloco de trabalho)
  parar    (Stop)             se a sessao trabalhou muito e nao delegou nada, segura o fim uma vez e pede
                              a justificativa ou a delegacao do que falta
  relatorio                   mostra, por sessao, quanto trabalho foi feito, quanto foi delegado e os avisos

Sessoes de executor (MAESTRO_EU definido pelo maestro, ou pasta em ~/.maestro/wt) sao ignoradas.
Limites ajustaveis por ambiente: MAESTRO_LIMITE_AVISO (12), MAESTRO_LIMITE_PARADA (20), MAESTRO_GUARDA=0 desliga.
"""
import json, os, sys, time

CASA = os.path.expanduser("~/.maestro")
PASTA = os.path.join(CASA, "guarda")
HISTORICO = os.path.join(CASA, "historico.jsonl")
LIMITE_AVISO = int(os.environ.get("MAESTRO_LIMITE_AVISO", "12"))
LIMITE_PARADA = int(os.environ.get("MAESTRO_LIMITE_PARADA", "20"))
# ferramentas que sao trabalho de execucao (o que o maestro poderia fazer mais barato)
TRABALHO = {"Edit", "Write", "MultiEdit", "NotebookEdit", "Read", "Grep", "Glob", "Bash", "PowerShell"}
# subagentes baratos tambem contam como delegacao
AGENTES_BARATOS = {"Explore", "implementador", "mecanico"}

CRITERIO = (
    "[maestro] Antes de agir, decida se esta tarefa vai para o maestro (`python ~/.maestro/maestro.py`). "
    "DELEGUE quando houver: implementar com especificacao clara, escrever ou rodar testes, refatorar, docs, "
    "edicao repetitiva em varios arquivos, ou ler logs/codigo grande (mais de ~300 linhas) para extrair fatos. "
    "NAO delegue: pergunta ou explicacao, edicao pequena (menos de ~20 linhas em 1 arquivo), decisao de "
    "arquitetura, depuracao que depende de testar ao vivo num aparelho a cada passo, ou dado sensivel sem "
    "--sensivel. Tarefa mista: voce decide e confere; a parte bracal desce. Declare na sua primeira linha: "
    "`maestro: sim - <o que vai delegar>` ou `maestro: nao - <motivo>`."
)


def executor(dados):
    if os.environ.get("MAESTRO_EU") or os.environ.get("MAESTRO_GUARDA") == "0":
        return True
    cwd = os.path.normcase(os.path.abspath(dados.get("cwd") or os.getcwd()))
    return cwd.startswith(os.path.normcase(os.path.join(CASA, "wt")))


def caminho(sessao):
    return os.path.join(PASTA, "".join(c for c in sessao if c.isalnum() or c in "-_")[:80] + ".json")


def carregar(sessao):
    try:
        with open(caminho(sessao), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"trabalho": 0, "desde_delegacao": 0, "delegacoes": 0, "avisos": 0, "parada": False,
                "inicio": time.strftime("%Y-%m-%dT%H:%M:%S")}


def salvar(sessao, est):
    os.makedirs(PASTA, exist_ok=True)
    est["atualizado"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = caminho(sessao) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(est, f)
    os.replace(tmp, caminho(sessao))


def registrar(evento, **campos):
    try:
        with open(HISTORICO, "a", encoding="utf-8") as f:
            f.write(json.dumps({"evento": evento, **campos, "data": time.strftime("%Y-%m-%dT%H:%M:%S")}) + "\n")
    except OSError:
        pass


def responder(evento, contexto):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": evento, "additionalContext": contexto}}))


def eh_delegacao(nome, entrada):
    texto = json.dumps(entrada or {})
    if nome in ("Bash", "PowerShell") and "maestro.py" in texto:
        return True
    if nome == "Skill" and "maestro" in texto:
        return False  # carregar a skill nao e delegar; so o maestro.py conta
    if nome in ("Agent", "Task") and (entrada or {}).get("subagent_type") in AGENTES_BARATOS:
        return True
    return False


def ao_prompt(dados):
    prompt = (dados.get("prompt") or "").strip()
    # mensagens curtas ("ok", "continua", "sim") nao sao tarefa nova
    if len(prompt) < 40 or prompt.startswith("/"):
        return
    est = carregar(dados.get("session_id", "?"))
    est["prompts"] = est.get("prompts", 0) + 1
    salvar(dados.get("session_id", "?"), est)
    responder("UserPromptSubmit", CRITERIO)


def ao_usar(dados):
    sessao, nome = dados.get("session_id", "?"), dados.get("tool_name", "")
    est = carregar(sessao)
    if eh_delegacao(nome, dados.get("tool_input")):
        est["delegacoes"] += 1
        est["desde_delegacao"] = 0
        salvar(sessao, est)
        return
    if nome not in TRABALHO:
        return
    est["trabalho"] += 1
    est["desde_delegacao"] += 1
    aviso = None
    if est["desde_delegacao"] >= LIMITE_AVISO and est["desde_delegacao"] % LIMITE_AVISO == 0:
        est["avisos"] += 1
        aviso = (
            f"[maestro] Voce ja fez {est['desde_delegacao']} chamadas de execucao (ler, buscar, editar, rodar) "
            f"sem delegar nada{' desde a ultima delegacao' if est['delegacoes'] else ''}. Reavalie agora: o que "
            "falta (ler logs, edicoes em lote, testes, implementacao com especificacao clara) pode ir para "
            "`python ~/.maestro/maestro.py`? Se for continuar sozinho, diga em 1 linha por que."
        )
    salvar(sessao, est)
    if aviso:
        responder("PostToolUse", aviso)


def ao_parar(dados):
    sessao = dados.get("session_id", "?")
    est = carregar(sessao)
    if dados.get("stop_hook_active") or est.get("parada") or est["delegacoes"] or est["trabalho"] < LIMITE_PARADA:
        return
    est["parada"] = True
    salvar(sessao, est)
    registrar("auditoria", sessao=sessao, trabalho=est["trabalho"], avisos=est["avisos"],
              repo=os.path.basename(dados.get("cwd") or os.getcwd()))
    print(json.dumps({"decision": "block", "reason": (
        f"[maestro] Auditoria: esta sessao fez {est['trabalho']} chamadas de execucao e nao delegou nada. "
        "Antes de encerrar: se ainda falta trabalho bracal (testes, docs, revisao de logs, limpeza), delegue "
        "pelo maestro agora. Se nao falta, responda em 1 a 2 linhas o que poderia ter ido para o maestro e por "
        "que nao foi, e encerre."
    )}))


def relatorio():
    if not os.path.isdir(PASTA):
        print("sem dados do guarda ainda")
        return
    linhas = []
    for nome in os.listdir(PASTA):
        if nome.endswith(".json"):
            try:
                with open(os.path.join(PASTA, nome), encoding="utf-8") as f:
                    e = json.load(f)
            except Exception:
                continue
            linhas.append((e.get("atualizado", ""), nome[:8], e))
    linhas.sort(reverse=True)
    print(f"{'atualizado':19}  sessao    trabalho  delegou  avisos  auditada")
    for data, sid, e in linhas[:30]:
        print(f"{data:19}  {sid:8}  {e['trabalho']:8}  {e['delegacoes']:7}  {e['avisos']:6}  "
              f"{'sim' if e.get('parada') else '-'}")
    total = sum(e["trabalho"] for _, _, e in linhas)
    sem = sum(1 for _, _, e in linhas if e["trabalho"] >= LIMITE_PARADA and not e["delegacoes"])
    print(f"\n{len(linhas)} sessoes, {total} chamadas de execucao; {sem} sessoes grandes sem nenhuma delegacao")


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    modo = sys.argv[1] if len(sys.argv) > 1 else ""
    if modo == "relatorio":
        return relatorio()
    try:
        dados = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return
    if executor(dados):
        return
    try:
        {"prompt": ao_prompt, "ferramenta": ao_usar, "parar": ao_parar}.get(modo, lambda d: None)(dados)
    except Exception as e:  # o guarda nunca pode travar a sessao
        print(f"guarda: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
