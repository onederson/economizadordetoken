#!/usr/bin/env python3
"""maestro: delega tarefas a modelos baratos (Claude, agy, opencode) num worktree isolado
e devolve so um resumo curto. O orquestrador especifica, revisa e decide se junta.

Uso:
  maestro.py catalogo                         executores, bolso de cota, estado
  maestro.py run --exec agy-flash-high --dir PROJ --tarefa t.md [--verificar "cmd"] [--tipo implementar]
  maestro.py run --exec agy-flash-high,omni-free-code ...   (cai para o proximo se a cota acabar)
  maestro.py run --continuar JOB "correcao objetiva"         (mesma sessao e worktree, rodada +1)
  maestro.py run --leitura --exec cc-haiku --dir PROJ "onde X e usado?"   (coleta, sem worktree)
  maestro.py diff JOB [--stat]                 o que o executor mudou
  maestro.py juntar JOB                        aplica no projeto real e apaga o worktree
  maestro.py descartar JOB                     apaga o worktree sem aplicar
  maestro.py jobs                              jobs abertos
  maestro.py cota ID --horas 120 | --liberar   marca ou libera cota esgotada
  maestro.py sugerir [tipo]                    taxa de acerto por executor (historico)
  maestro.py ganho [--dias 30] [--comparar]    quanto os executores processaram, quanto voltou, onde se perdeu
  maestro.py validar --modelo opus|fable "o que foi pedido" [--base REF | --job JOB]   revisao de senior; so leitura

Niveis (qualquer modelo pode orquestrar; --eu e o seu id no catalogo ou o seu nivel de 1 a 4):
  maestro.py rota --eu cc-haiku [--tipo implementar]         quem fica abaixo/igual e quem fica acima de voce
  maestro.py run --eu 2 ...                                  delega so para nivel igual ou menor que o seu
  maestro.py consultar --eu 2 --dir PROJ "duvida objetiva"   pergunta ao modelo mais barato ACIMA de voce (so leitura)
  O executor que trava responde "ESCALAR: pergunta". Se quem chamou e maior, a pergunta volta para ele
  (saida 4); senao o maestro consulta um modelo acima e continua a mesma sessao (--escalar N, padrao 1).

Dados em ~/.maestro (estado.json, historico.jsonl, jobs/, wt/).
Se o --verificar falha, o maestro devolve a falha ao executor e roda de novo (--tentativas, padrao 2), ate
passar, repetir a mesma falha, chegar a 3 rodadas no job ou estourar o --orcamento de tokens.
Saida do run: 0 ok, 1 erro, 2 timeout, 3 verificacao falhou, 4 executor parou com duvida,
5 modelo local nao cabe e nao deu para abrir a janela (pergunte ao usuario; ver abaixo).

Modelo local (ollama): antes de rodar, o maestro confere RAM + VRAM livres. Se nao cabe, o modelo local tem
prioridade: uma janela avisa quais apps vao ser fechados. 'Fechar' ou sem resposta em 2 min: fecha, roda e reabre
os apps quando o maestro termina. 'Nao fechar': segue a rota sem o local. Sem janela (ou --sem-janela), para com
saida 5 e lista os apps; ai quem orquestra NUNCA fecha nada sem o usuario autorizar:
  maestro.py recursos [--exec ID]              cabe ou nao, quanto falta, apps que liberariam memoria
  maestro.py liberar --apps chrome,discord     fecha so esses (graceful; --forcar mata o que nao fechou)
  maestro.py run ... --pular-local             usuario nao autorizou: segue para o proximo executor da rota
  maestro.py reabrir                           reabre o que o maestro fechou e ainda nao reabriu
"""
import argparse, collections, hashlib, json, os, re, secrets, shutil, subprocess, sys, time, tomllib, urllib.error, urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
CASA = os.path.expanduser("~/.maestro")
CATALOGO = os.path.join(AQUI, "catalogo.toml")
ESTADO = os.path.join(CASA, "estado.json")
HISTORICO = os.path.join(CASA, "historico.jsonl")
TAREFA = ".maestro-tarefa.md"
# lixo de build/execucao que nunca vai para o patch, mesmo sem .gitignore
LIXO = [f":(exclude,glob){g}" for g in ("**/__pycache__/**", "**/*.pyc", "**/node_modules/**", "**/.pytest_cache/**", "**/*.log")]
BOLSOS = ["local", "omniroute", "google", "anthropic"]  # ordem de gasto, se o catalogo nao disser outra
ESCALAR_RE = re.compile(r"^\s*ESCALAR:\s*", re.M)
SINAL_RE = re.compile(r"error|erro\b|fail|falh|exception|traceback|assert|panic|fatal|denied|not found|"
                      r"nao encontrad|warning|^E\s|^\s*File \"", re.I)
ARQ_RE = re.compile(r"`[^`]+`|[\w./\\-]+\.[A-Za-z]{1,5}\b")
VAZIA_RE = re.compile(r"^Ran 0 tests?\b|\bNO TESTS RAN\b|\bno tests ran\b|\bcollected 0 items\b|\bNo tests found\b|"
                      r"\bno test files\b|^0 passing\b", re.I | re.M)
COTA_RE = re.compile(r"\b40[23]\b|insufficient (?:credit|balance|quota)|out of credits|requires usage credits|no credits? (?:left|remaining)|payment required|all models? (?:failed|unavailable)|\b429\b|RESOURCE_EXHAUSTED|quota|rate.?limit|usage limit|exhausted|limite de uso|compaction\.failed", re.I)

LEITURA = (
    "Voce coleta informacao para outro modelo. Leia o arquivo {arq} e faca o que ele pede. "
    "NAO altere nenhum arquivo nem o estado do sistema; comandos so de leitura. Devolva fatos, nao opinioes: "
    "caminho:linha, mensagens e numeros literais, o que procurou e nao achou. Nao imprima chaves ou tokens. "
    "Seja curto: no maximo 40 linhas. Nao use a skill maestro nem chame maestro.py. A pasta do projeto (trabalhe nela) e: {pasta}"
)

CONSULTA = (
    "Voce e o modelo mais forte, consultado por um modelo menor que precisa de uma decisao. Leia o arquivo {arq} "
    "e responda a duvida. A pasta do projeto e: {pasta}. Leia so o que for preciso para decidir. "
    "NAO altere nenhum arquivo; comandos so de leitura. Quem executa e o modelo menor: entregue a decisao pronta "
    "para ele seguir, sem fazer o trabalho dele. Responda em no maximo 25 linhas, nesta ordem: DECISAO, "
    "MOTIVO (1 a 3 linhas), PASSOS numerados com arquivo e funcao, RISCOS. Nao imprima chaves ou tokens. "
    "Nao use a skill maestro nem chame maestro.py."
)

VALIDA = (
    "Voce e um engenheiro de software senior com mais de 50 anos de experiencia. Outro modelo fez um trabalho e "
    "voce decide, com provas, se ele pode ser entregue. Leia o arquivo {arq}: tem o que o usuario pediu, a "
    "especificacao dada ao executor (se houver), a validacao anterior (se houver), o que foi alterado e onde esta o "
    "diff completo. A pasta do projeto e: {pasta}. NAO altere nenhum arquivo: voce so julga. Nao confie no que o "
    "outro modelo disse que fez: confira no codigo.\n\n"
    "Ordem: 0) se ha VALIDACAO ANTERIOR, confira primeiro cada problema dela (corrigido ou nao, com caminho:linha). "
    "1) o pedido foi atendido por inteiro? Cada requisito nas palavras do usuario; o que falta; o que foi feito sem "
    "ser pedido (servico externo, rede, upload, arquivo grande, mudanca de comportamento global: isso e grave). "
    "2) o codigo esta correto? Para cada estado que o trabalho abre (recurso, conexao, toque, lock, arquivo, "
    "processo, flag), confira que fecha em TODOS os caminhos: sucesso, erro, cancelamento, timeout, entrada "
    "invalida, troca de modo no meio. Leia quem chama o que mudou: efeito duplicado, contrato quebrado. "
    "3) testes: rode os testes e verificacoes do projeto e cite o resultado literal; os testes cobrem os caminhos "
    "de falha ou so o caminho feliz? 4) seguranca, segredos, sobras (debug, temporarios, TODO). Diff grande: "
    "priorize rede/servicos externos, apagar dados, credenciais, concorrencia e maquinas de estado, e diga o que "
    "nao leu.\n\n"
    "Regua (siga a risca, ela decide o veredito):\n"
    "- [bloqueia]: no uso pedido, da resultado errado, perde ou expoe dado, trava, ou faz algo nao pedido com "
    "efeito externo ou irreversivel.\n"
    "- [importante]: bug real num cenario plausivel do uso pedido, ou teste que deixaria passar esse bug.\n"
    "- Para [bloqueia] e [importante] de o cenario concreto (entrada/sequencia -> resultado errado); se conseguir, "
    "reproduza com um script e cite a saida. Sem cenario concreto, nao e problema: e no maximo sugestao.\n"
    "- Aponte a CLASSE, nao so a ocorrencia: achou um caminho que deixa estado aberto, procure TODOS os outros "
    "caminhos do mesmo tipo e liste todos no mesmo item, com a invariante que a correcao deve garantir e o teste "
    "que pegaria a classe inteira. Correcao que conserta 1 de 4 caminhos gera outra rodada.\n"
    "- Origem de cada problema: [spec] o pedido/especificacao nao disse e o executor nao tinha como saber; "
    "[executor] estava dito, ou e engenharia basica; [antigo] ja existia antes deste trabalho.\n"
    "- NAO sao problemas e nao pesam no veredito (vao em FORA DO VEREDITO, 1 linha cada): [antigo]; o que so se "
    "confirma ao vivo (aparelho, conta, rede real) e ja foi declarado pendente; estilo, nomes, redundancia, espaco "
    "no fim da linha, variavel sem uso; melhorias que ninguem pediu.\n"
    "- Revalidacao: leia primeiro o diff desde a validacao anterior (a correcao). Problema novo so entra se for "
    "[bloqueia] ou [importante] e voce disser se veio da correcao ou se ja existia e escapou antes. Item que o "
    "usuario decidiu nao corrigir nao volta. Nao suba a regua a cada rodada.\n"
    "- VEREDITO: REPROVADO se houver [bloqueia]; APROVADO COM RESSALVAS se houver [importante]; senao APROVADO. "
    "APROVADO e o resultado esperado para trabalho correto: nao invente ressalva para parecer rigoroso.\n\n"
    "Responda em no maximo 60 linhas, neste formato:\n"
    "VEREDITO: APROVADO | APROVADO COM RESSALVAS | REPROVADO\n"
    "RESUMO: 1 a 2 linhas\n"
    "ANTERIOR: (so em revalidacao) cada item anterior -> corrigido / nao corrigido, caminho:linha\n"
    "PROBLEMAS: numerados, cada um com [bloqueia|importante] [spec|executor], todas as ocorrencias em "
    "caminho:linha, cenario concreto, invariante a garantir e teste que prova; ou 'nenhum'\n"
    "FORA DO VEREDITO: ate 5 linhas; ou 'nada'\n"
    "LICOES: para cada problema [spec], 1 linha generica e reutilizavel que, posta na especificacao de tarefas "
    "futuras deste projeto, teria evitado o problema (comece cada uma com '- '; nada de caminho:linha); ou 'nenhuma'\n"
    "CONFERIDO: comandos que rodou com o resultado literal, o que leu e o que NAO leu\n"
    "Nao imprima chaves ou tokens. Nao use a skill maestro nem chame maestro.py."
)

# Revisores do 'validar': fora do catalogo de proposito, para o consultar e as rotas nunca gastarem eles sozinhos
VALIDADORES = {
    "opus": {"cli": "claude", "modelo": "claude-opus-5-5", "esforco": "medium", "cota": "anthropic", "custo": "caro",
             "nivel": 4, "privado": True},
    "fable": {"cli": "claude", "modelo": "claude-fable-5-2", "esforco": "medium", "cota": "anthropic", "custo": "caro",
              "nivel": 4, "privado": True},
}
VEREDITO_RE = re.compile(r"VEREDITO:\W*(APROVADO COM RESSALVAS|APROVADO|REPROVADO)", re.I)

REGRAS = (
    "Voce e um executor. Leia o arquivo " + TAREFA + " nesta pasta e execute exatamente o que ele pede. "
    "Regras: nao saia do escopo nem melhore o que nao foi pedido; nao faca commit, push, reset nem checkout no git; "
    "nao rode nada em segundo plano; nao imprima chaves ou tokens; nao instale pacotes se a tarefa nao pedir; "
    "nao use a skill maestro nem chame maestro.py (voce e o executor, nao delegue). "
    "Ao terminar, responda SOMENTE com um bloco que comece com RESULTADO: e tenha no maximo 8 linhas: "
    "arquivos alterados, o que foi feito, comandos de teste rodados com o resultado literal, duvidas. "
    "Nunca diga que testou algo que nao rodou. "
    "Se faltar uma decisao que voce nao consegue tomar com seguranca (tarefa ambigua, duas abordagens com "
    "consequencias diferentes, causa de um erro ainda incerta depois de 2 tentativas), nao chute: pare e responda "
    "SOMENTE com um bloco que comece com ESCALAR: e tenha no maximo 12 linhas: a pergunta objetiva, o que voce ja "
    "verificou e as opcoes que ve. Um modelo mais forte responde e voce continua."
)

for s in (sys.stdout, sys.stderr):
    try:
        s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ---------- utilidades ----------

def ler_json(caminho, padrao):
    try:
        with open(caminho, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return padrao


def gravar_json(caminho, dados):
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    tmp = caminho + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dados, f, indent=2, ensure_ascii=False)
    os.replace(tmp, caminho)


def registrar(evento):
    os.makedirs(CASA, exist_ok=True)
    evento["data"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    with open(HISTORICO, "a", encoding="utf-8") as f:
        f.write(json.dumps(evento, ensure_ascii=False) + "\n")


def git(pasta, *args, ok=False):
    r = subprocess.run(["git", *args], cwd=pasta, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
    if ok and r.returncode != 0:
        sys.exit(f"git {' '.join(args)} falhou: {r.stderr.strip()[:400]}")
    return r


def ler_catalogo():
    with open(CATALOGO, "rb") as f:
        return tomllib.load(f)


def catalogo():
    return {k: v for k, v in ler_catalogo().items() if isinstance(v, dict) and "cli" in v}


def nivel(ex):
    """1 leitura/mecanico, 2 implementa com especificacao, 3 implementacao delicada, 4 decide."""
    return ex.get("nivel") or {"barato": 2, "medio": 3, "caro": 3}.get(ex.get("custo"), 2)


def meu_nivel(spec, cat):
    """--eu (id do catalogo ou numero) ou MAESTRO_EU, que o maestro define para o executor que ele lanca."""
    spec = spec or os.environ.get("MAESTRO_EU")
    if not spec:
        return None
    if spec.isdigit():
        return int(spec)
    if spec in cat:
        return nivel(cat[spec])
    sys.exit(f"--eu desconhecido: {spec} (use um id do catalogo ou um nivel de 1 a 4)")


def profundidade():
    try:
        return int(os.environ.get("MAESTRO_PROFUNDIDADE", "0"))
    except ValueError:
        return 0


def ambiente(env, pasta, eid):
    # AI_MEMORY_AUTO_HANDOFF=off: o hook do ai-memory (que o agy chama) nao cria handoff no fim da sessao do
    # executor; sem isso, cada job deixava um handoff aberto que caia na proxima sessao do usuario
    return {**env, "PWD": pasta, "MAESTRO_EU": eid, "MAESTRO_PROFUNDIDADE": str(profundidade() + 1),
            "AI_MEMORY_AUTO_HANDOFF": "off"}


def acima(n, cat, sensivel=False, minimo=None):
    """Executores de nivel maior que n: o mais baixo que resolve primeiro, dentro do nivel a ordem de gasto,
    e dentro do bolso o mais barato (custo) antes do caro."""
    bolsos = ler_catalogo().get("bolsos", BOLSOS)
    pos = lambda b: bolsos.index(b) if b in bolsos else len(bolsos)
    preco = {"barato": 0, "medio": 1, "caro": 2}
    piso = max(n + 1, minimo or 0)
    ids = [e for e, ex in cat.items() if nivel(ex) >= piso and (ex.get("privado", True) or not sensivel)]
    return sorted(ids, key=lambda e: (nivel(cat[e]), pos(cat[e]["cota"]), preco.get(cat[e].get("custo"), 1)))


ACERTO_MIN = 0.5      # abaixo disso o executor vai para o fim da rota daquele tipo
# servico fora do ar nao e culpa do modelo: pula por FORA_DO_AR_H horas sem contar derrota no aprendizado
FORA_DO_AR_RE = re.compile(r"ECONNREFUSED|ECONNRESET|connection refused|Unable to connect|getaddrinfo|ENOTFOUND|"
                           r"actively refused|HTTP[ /]\d?\.?\d? ?50[234]|status(?: code)?:? ?50[234]|Bad Gateway|Service Unavailable", re.I)
FORA_DO_AR_H = 0.5
VERIF_QUEBRADA_RE = re.compile(r"is not recognized as an internal or external command|n.o . reconhecido como um comando|"
                               r"^\S*: line \d+: \S+: command not found|^\S+: \S+: No such file or directory$|"
                               r"can't open file .*No such file", re.I | re.M)
ACERTO_AMOSTRA = 3    # tentativas julgadas antes de comparar alguem (falha custa minutos: aprende rapido)
ACERTO_MARGEM = 0.15  # quem fica mais que isso abaixo do melhor da rota cede a vez a ele
SEQUENCIA_RUIM = 3    # tantas tentativas perdidas seguidas (qualquer tipo) = vai para o fim na hora
ACERTO_DIAS = 30      # so o recente conta, para um executor rebaixado poder voltar quando o historico envelhece
GRATIS = ("local", "omniroute")
RODADAS_MAX = 3  # rodadas por job somando as automaticas; dali em diante quem decide e o orquestrador


def _tentativas(dias=ACERTO_DIAS):
    """Aprendizado: cada (job, executor) e uma tentativa. Ganha so quem entregou o trabalho que foi juntado.
    Perde quem falhou, estourou o tempo, nao mudou nada, foi substituido por outro na correcao ou teve o job
    descartado. Antes so o executor final do job contava: o local que falhava e era salvo pelo agy nunca
    perdia. Devolve lista cronologica de dicts {exec, tipo, job, data, ganhou, seg}."""
    desde = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - dias * 86400))
    ev = []
    try:
        with open(HISTORICO, encoding="utf-8") as f:
            for linha in f:
                try:
                    e = json.loads(linha)
                except ValueError:
                    continue
                if e.get("data", "") >= desde:
                    ev.append(e)
    except OSError:
        pass
    tipo_job, finais = {}, {}
    for e in ev:
        if e.get("job") and e.get("evento") == "run":
            tipo_job.setdefault(e["job"], e.get("tipo") or "implementar")
        if e.get("evento") in ("juntar", "descartar") and e.get("job"):
            finais[e["job"]] = e
            tipo_job.setdefault(e["job"], e.get("tipo") or "implementar")
    pares = {}  # (job, exec) -> tentativa
    for e in ev:
        jb, ex, evento = e.get("job"), e.get("exec"), e.get("evento")
        if not jb or not ex or evento not in ("run", "falha_local"):
            continue
        t = pares.setdefault((jb, ex), {"exec": ex, "job": jb, "tipo": e.get("tipo") or tipo_job.get(jb, "implementar"),
                                        "data": e.get("data", ""), "seg": 0, "falhou": False})
        t["data"] = max(t["data"], e.get("data", ""))
        t["seg"] += e.get("segundos") or 0
        if evento == "falha_local" or e.get("status") in ("ERRO", "TIMEOUT"):
            t["falhou"] = True
    desde_exec = vigencia_config()
    saida = []
    for (jb, ex), t in pares.items():
        if t["data"] < desde_exec.get(ex, ""):
            continue  # tentativa de antes da configuracao atual do executor (outro modelo/contexto)
        fim = finais.get(jb)
        if fim:
            t["ganhou"] = fim["evento"] == "juntar" and fim.get("exec") == ex
        elif t["falhou"]:
            t["ganhou"] = False  # job ainda aberto, mas este executor ja falhou nele
        else:
            continue  # job aberto e sem falha dele: ainda nao da para julgar
        saida.append(t)
    saida.sort(key=lambda t: t["data"])
    return saida


CONFIG_CHAVES = ("cli", "modelo", "contexto", "esforco", "url", "max_saida")


def vigencia_config():
    """{executor: data desde a qual o historico vale}. Quando o modelo ou a configuracao de um executor muda no
    catalogo, o historico antigo dele deixa de contar: julga o executor novo, nao o que estava mal configurado.
    Executor visto pela primeira vez mantem o historico."""
    try:
        cat = ler_catalogo()
    except Exception:
        return {}
    est = ler_json(ESTADO, {})
    vig = est.setdefault("config_exec", {})
    agora = time.strftime("%Y-%m-%dT%H:%M:%S")
    mudou = False
    for eid, ex in cat.items():
        if not isinstance(ex, dict) or "cli" not in ex:
            continue
        h = marca(json.dumps({k: ex.get(k) for k in CONFIG_CHAVES}, sort_keys=True))
        v = vig.get(eid)
        if v is None:
            vig[eid], mudou = {"hash": h, "desde": ""}, True
        elif v["hash"] != h:
            vig[eid], mudou = {"hash": h, "desde": agora}, True
            print(f"aprendizado: {eid} mudou de configuracao; o historico anterior dele deixa de contar")
    if mudou:
        gravar_json(ESTADO, est)
    return {e: v["desde"] for e, v in vig.items()}


def acerto(tipo, dias=ACERTO_DIAS, tentativas=None):
    """{executor: [ganhou, tentativas, segundos_somados_das_vitorias]} no tipo, nos ultimos `dias`."""
    tipo = tipo or "implementar"
    placar = collections.defaultdict(lambda: [0, 0, 0])
    for t in (_tentativas(dias) if tentativas is None else tentativas):
        if t["tipo"] == tipo:
            p = placar[t["exec"]]
            p[0] += t["ganhou"]
            p[1] += 1
            p[2] += t["seg"] if t["ganhou"] else 0
    return placar


def em_sequencia_ruim(eid, tentativas, n=SEQUENCIA_RUIM):
    """As ultimas n tentativas do executor (qualquer tipo) foram todas perdidas: ambiente ou modelo quebrado.
    Pega o problema na hora, sem esperar a amostra do tipo encher."""
    dele = [t for t in tentativas if t["exec"] == eid][-n:]
    return len(dele) >= n and not any(t["ganhou"] for t in dele)


def ordenar_por_acerto(lista, tipo, silencioso=False):
    """Aprende com o historico e reordena a rota do catalogo (que segue a ordem de gasto do usuario).
    Vai para o fim, de reserva para falta de cota, quem:
      - tem amostra (ACERTO_AMOSTRA) e acerta menos que ACERTO_MIN, ou fica mais de ACERTO_MARGEM abaixo do
        melhor executor da rota (ex.: o local que perde para o agy cede a vez ao agy);
      - perdeu as ultimas SEQUENCIA_RUIM tentativas, em qualquer tipo.
    Qualidade vem antes de custo: token gratis que vira descarte custa token caro de quem revisa e refaz.
    O historico envelhece (ACERTO_DIAS), entao quem foi rebaixado volta a ser testado depois."""
    tents = _tentativas()
    placar = acerto(tipo, tentativas=tents)
    taxa = {e: placar[e][0] / placar[e][1] for e in lista if placar.get(e, [0, 0])[1] >= ACERTO_AMOSTRA}
    melhor = max(taxa.values()) if taxa else None
    fracos, motivo = [], {}
    for e in lista:
        if e in taxa and (taxa[e] < ACERTO_MIN or taxa[e] < melhor - ACERTO_MARGEM):
            fracos.append(e)
            motivo[e] = (f"{placar[e][0]}/{placar[e][1]} tentativas aproveitadas em '{tipo or 'implementar'}'"
                         f" nos ultimos {ACERTO_DIAS} dias; o melhor da rota tem {round(100 * melhor)}%")
        elif em_sequencia_ruim(e, tents):
            fracos.append(e)
            motivo[e] = f"perdeu as ultimas {SEQUENCIA_RUIM} tentativas"
    if not fracos or len(fracos) == len(lista):
        return lista
    if not silencioso:
        for e in fracos:
            print(f"rota: {e} vai para o fim ({motivo[e]})")
    fracos.sort(key=lambda e: -taxa.get(e, 0))
    return [e for e in lista if e not in fracos] + fracos


def resolver(spec, tipo, cat, sensivel=False, teto=None):
    """'auto' ou 'a,b,c' -> lista de executores, ja sem os externos se sensivel.
    teto: nivel de quem chama. Na rota automatica so fica quem e igual ou menor; a rota 'dificil' e a
    excecao, porque e o caminho explicito para executar acima."""
    auto = spec in (None, "", "auto")
    if auto:
        rotas = ler_catalogo().get("rotas", {})
        lista = rotas.get(tipo or "implementar") or rotas.get("implementar", [])
    else:
        lista = [e for e in spec.split(",") if e]
    for e in lista:
        if e not in cat:
            sys.exit(f"executor desconhecido: {e} (veja 'maestro.py catalogo')")
    if sensivel:
        lista = [e for e in lista if cat[e].get("privado", True)]
    if teto and auto and tipo != "dificil":
        lista = [e for e in lista if nivel(cat[e]) <= teto]
        if not lista:
            sys.exit(f"nenhum executor de nivel <= {teto} na rota '{tipo or 'implementar'}'. Para subir: "
                     "'maestro.py consultar' (duvida) ou '--tipo dificil' (execucao)")
    if not lista:
        sys.exit("nenhum executor na rota")
    return ordenar_por_acerto(lista, tipo) if auto else lista


def esgotados():
    agora = time.strftime("%Y-%m-%dT%H:%M")
    est = ler_json(ESTADO, {})
    return {k: v for k, v in est.get("esgotado", {}).items() if v > agora}


def marcar_esgotado(eid, horas):
    est = ler_json(ESTADO, {})
    ate = time.strftime("%Y-%m-%dT%H:%M", time.localtime(time.time() + horas * 3600))
    est.setdefault("esgotado", {})[eid] = ate
    gravar_json(ESTADO, est)
    return ate


def job_dir(job):
    return os.path.join(CASA, "jobs", job)


def carregar_job(job):
    j = ler_json(os.path.join(job_dir(job), "job.json"), None)
    if not j:
        sys.exit(f"job {job} nao encontrado")
    return j


def salvar_job(j):
    gravar_json(os.path.join(job_dir(j["job"]), "job.json"), j)


# ---------- worktree ----------

# Marcador do ai-memory na pasta dos worktrees (a busca sobe do cwd ate a home, entao vale para todos).
# A sessao do executor continua registrada no projeto, mas nao ensina habitos ao perfil do usuario (as regras
# do executor, como "nao use o maestro", viravam padrao do orquestrador), nao recebe o resumo do perfil e
# nao consome o handoff que era do orquestrador.
MARCADOR_AI_MEMORY = (
    "# Criado pelo maestro: sessoes de executor nao entram no perfil nem consomem handoff.\n"
    "[profile]\ncontribute = false\nconsume = false\n\n"
    "[handoff]\ndeliver_on_session_start = false\n"
)


def garantir_marcador(pasta):
    caminho = os.path.join(pasta, ".ai-memory.toml")
    if not os.path.exists(caminho):
        with open(caminho, "w", encoding="utf-8") as f:
            f.write(MARCADOR_AI_MEMORY)


def criar_worktree(repo, job):
    wt = os.path.join(CASA, "wt", job)
    os.makedirs(os.path.dirname(wt), exist_ok=True)
    garantir_marcador(os.path.dirname(wt))
    base = git(repo, "stash", "create").stdout.strip() or git(repo, "rev-parse", "HEAD", ok=True).stdout.strip()
    git(repo, "worktree", "add", "--detach", wt, base, ok=True)
    # arquivos novos do usuario (nao rastreados) tambem vao para o worktree
    soltos = [p for p in git(repo, "ls-files", "--others", "--exclude-standard", "-z").stdout.split("\0") if p]
    total = sum(os.path.getsize(os.path.join(repo, p)) for p in soltos if os.path.isfile(os.path.join(repo, p)))
    aviso = None
    if soltos and len(soltos) <= 2000 and total <= 200 * 2**20:
        for p in soltos:
            destino = os.path.join(wt, p)
            os.makedirs(os.path.dirname(destino), exist_ok=True)
            shutil.copy2(os.path.join(repo, p), destino)
    elif soltos:
        aviso = f"{len(soltos)} arquivos nao rastreados ({total // 2**20} MB) NAO foram copiados para o worktree"
    git(wt, "add", "-A")
    if git(wt, "diff", "--cached", "--quiet").returncode != 0:
        git(wt, "-c", "user.name=maestro", "-c", "user.email=maestro@local", "commit", "-q", "--no-verify",
            "-m", "maestro: base", ok=True)
    return wt, git(wt, "rev-parse", "HEAD", ok=True).stdout.strip(), aviso


def mudancas(j, binario=False, stat=False, caminho=None):
    wt = j["wt"]
    p = os.path.join(wt, TAREFA)
    if os.path.exists(p):
        os.remove(p)
    git(wt, "add", "-A")
    args = ["diff", "--cached"] + (["--stat"] if stat else []) + (["--binary"] if binario else []) + [j["base"], "--", caminho or "."] + LIXO
    saida = git(wt, *args).stdout
    git(wt, "reset", "-q")
    return saida


def _desfazer_links(raiz):
    """Remove symlinks e junctions dentro do worktree SEM seguir o destino.

    No Windows com Python < 3.12, shutil.rmtree (e o git worktree remove)
    entram numa junction e apagam a pasta real para onde ela aponta. Em
    2026-11-10 isso esvaziou client/, server/ e node_modules/ de um projeto.
    """
    REPARSE = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT
    if not os.path.isdir(raiz):
        return
    pilha = [raiz]
    while pilha:
        atual = pilha.pop()
        try:
            entradas = list(os.scandir(atual))
        except OSError:
            continue
        for e in entradas:
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            link = e.is_symlink() or bool(getattr(st, "st_file_attributes", 0) & REPARSE)
            if link:
                try:
                    os.rmdir(e.path)  # junction / symlink de pasta: remove so o link
                except OSError:
                    try:
                        os.unlink(e.path)
                    except OSError:
                        pass
            elif e.is_dir(follow_symlinks=False):
                pilha.append(e.path)


def foto_wt(wt):
    """Estado atual do worktree (rastreado + nao rastreado) como um tree do git, sem mudar nada."""
    git(wt, "add", "-A")
    t = git(wt, "write-tree").stdout.strip()
    git(wt, "reset", "-q")
    return t


def restaurar_wt(wt, tree):
    """Volta o worktree ao tree da foto: o lixo de um executor que falhou nao contamina o proximo."""
    if not tree:
        return
    git(wt, "add", "-A")
    git(wt, "read-tree", "-u", "--reset", tree)
    git(wt, "reset", "-q")


def remover_worktree(j):
    _desfazer_links(j["wt"])
    git(j["repo"], "worktree", "remove", "--force", j["wt"])
    if os.path.isdir(j["wt"]):
        shutil.rmtree(j["wt"], ignore_errors=True)
    git(j["repo"], "worktree", "prune")


# ---------- adaptadores ----------

def garantir_ollama(url="http://127.0.0.1:11434"):
    api = f"{url.rstrip('/')}/api/version"
    try:
        with urllib.request.urlopen(api, timeout=1.5) as r:
            if r.status == 200:
                return True
    except Exception:
        pass
    exe = shutil.which("ollama")
    if not exe:
        cand = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe")
        if os.path.isfile(cand):
            exe = cand
    if not exe:
        return False
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        kwargs["creationflags"] = flags
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen([exe, "serve"], **kwargs)
    except Exception:
        return False
    for _ in range(16):
        time.sleep(0.5)
        try:
            with urllib.request.urlopen(api, timeout=1.5) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
    return False


# ---------- folga do PC para modelo local ----------
# Antes de um executor ollama rodar, o maestro confere se a RAM + VRAM livres comportam o modelo. Se nao comportam,
# o modelo local tem prioridade: uma janela avisa que vai fechar os apps necessarios (zero token do orquestrador).
# 'Fechar' ou sem resposta: fecha, roda e reabre tudo no fim. 'Nao fechar': segue a rota sem o local. Sem janela
# possivel, o run para com saida 5 e quem orquestra pergunta ao usuario.
GB = 1024 ** 3
FOLGA_SO = 2.5 * GB      # o que fica reservado ao sistema e aos apps que continuam abertos
JANELA_ESPERA = 120      # segundos que a janela espera; sem resposta = fecha e roda o modelo local
PROTEGIDOS = {  # nunca entram na lista de fechar: sistema, shell, sessoes de IA e o proprio ollama
    "system", "idle", "registry", "smss", "csrss", "wininit", "winlogon", "services", "lsass", "lsaiso", "svchost",
    "dwm", "explorer", "fontdrvhost", "sihost", "ctfmon", "taskhostw", "runtimebroker", "searchhost", "searchindexer",
    "startmenuexperiencehost", "shellexperiencehost", "textinputhost", "lockapp", "memory compression", "audiodg",
    "conhost", "openconsole", "windowsterminal", "wt", "pwsh", "powershell", "cmd", "bash", "sh", "zsh", "python",
    "python3", "pythonw", "py", "ollama", "ollama app", "ollama_llama_server", "llama-server", "claude", "agy",
    "antigravity", "codex", "opencode", "ai-memory", "msmpeng", "mpdefendercoreservice", "securityhealthsystray",
    "securityhealthservice", "nvcontainer", "nvdisplay.container", "spoolsv", "wudfhost", "dllhost", "smartscreen",
    "systemd", "init", "kthreadd", "launchd", "kernel_task", "windowserver", "loginwindow", "xorg", "gnome-shell",
    "plasmashell", "sshd", "login", "tmux", "screen",
}


def _nome_proc(nome):
    n = (nome or "").strip().lower()
    return n[:-4] if n.endswith(".exe") else n


def memoria_livre():
    """(ram_livre, ram_total, vram_livre, vram_total) em bytes; vram 0 sem GPU NVIDIA."""
    ram_livre = ram_total = 0
    if os.name == "nt":
        import ctypes

        class _Mem(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        m = _Mem()
        m.dwLength = ctypes.sizeof(_Mem)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
            ram_livre, ram_total = m.ullAvailPhys, m.ullTotalPhys
    elif os.path.exists("/proc/meminfo"):
        info = {}
        with open("/proc/meminfo") as f:
            for l in f:
                k, _, v = l.partition(":")
                info[k] = int(v.split()[0]) * 1024
        ram_livre, ram_total = info.get("MemAvailable", info.get("MemFree", 0)), info.get("MemTotal", 0)
    else:  # macOS
        try:
            ram_total = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout)
            vm = subprocess.run(["vm_stat"], capture_output=True, text=True).stdout
            pag = int(re.search(r"page size of (\d+)", vm).group(1))
            ram_livre = sum(int(re.search(rf"{k}:\s+(\d+)", vm).group(1)) for k in ("Pages free", "Pages inactive")) * pag
        except Exception:
            pass
    vram_livre = vram_total = 0
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free,memory.total", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=10).stdout
            for l in out.strip().splitlines():
                livre, total = (int(x.strip()) * 1024 ** 2 for x in l.split(","))
                vram_livre, vram_total = vram_livre + livre, vram_total + total
        except Exception:
            pass
    return ram_livre, ram_total, vram_livre, vram_total


def processos():
    """[(pid, ppid, nome, memoria_bytes, sessao, executavel, linha_de_comando)] da maquina."""
    if os.name == "nt":
        ps = ("Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,WorkingSetSize,SessionId,"
              "ExecutablePath,CommandLine | ConvertTo-Json -Compress")
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True,
                                 text=True, encoding="utf-8", errors="replace", timeout=60).stdout
            dados = json.loads(out or "[]")
        except Exception:
            return []
        if isinstance(dados, dict):
            dados = [dados]
        return [(d["ProcessId"], d["ParentProcessId"], d.get("Name") or "", int(d.get("WorkingSetSize") or 0),
                 d.get("SessionId"), d.get("ExecutablePath") or "", d.get("CommandLine") or "") for d in dados]
    try:
        out = subprocess.run(["ps", "-eo", "pid=,ppid=,rss=,comm=,args="], capture_output=True, text=True,
                             timeout=30).stdout
    except Exception:
        return []
    lista = []
    for l in out.splitlines():
        partes = l.split(None, 4)
        if len(partes) >= 4:
            args = partes[4] if len(partes) == 5 else partes[3]
            lista.append((int(partes[0]), int(partes[1]), os.path.basename(partes[3]), int(partes[2]) * 1024, 1,
                          args.split()[0] if args else "", args))
    return lista


def _ancestrais(procs):
    """PIDs desta cadeia (maestro -> terminal/sessao que o chamou): fechar um deles derrubaria a propria tarefa."""
    pai = {p[0]: p[1] for p in procs}
    vistos, pid = set(), os.getpid()
    while pid and pid not in vistos:
        vistos.add(pid)
        pid = pai.get(pid)
    return vistos


# quem abre apps sem ser dono deles: um app cujo pai e um destes e um app de verdade (raiz do grupo)
LANCADORES = {"explorer", "svchost", "services", "wininit", "winlogon", "userinit", "sihost", "runtimebroker",
              "dllhost", "taskhostw", "smss", "system", "systemd", "init", "launchd"}


def apps_liberaveis(procs=None, minimo=150 * 1024 ** 2):
    """{app: (memoria_total, [pids], [(executavel, linha_de_comando)] das raizes)} dos apps que podem ser
    fechados, do maior para o menor. Cada processo conta para o app que o abriu (steamwebhelper -> steam);
    processo de um app protegido (webview do menu Iniciar, filhos do terminal) fica de fora."""
    procs = processos() if procs is None else procs
    anc = _ancestrais(procs)
    info = {p[0]: p for p in procs}
    nomes_anc = {_nome_proc(p[2]) for p in procs if p[0] in anc}
    grupos = {}
    for p in procs:
        pid, nome, mem, sessao = p[0], _nome_proc(p[2]), p[3], p[4]
        if not nome or pid == 0 or pid in anc or nome in PROTEGIDOS or sessao == 0:
            continue
        raiz, vistos = pid, set()
        while True:
            vistos.add(raiz)
            pai = info.get(info[raiz][1])
            if not pai or pai[0] in vistos or pai[0] == 0 or _nome_proc(pai[2]) in LANCADORES:
                break  # raiz: aberto pelo sistema/explorer, ou o pai ja morreu
            if pai[0] in anc or _nome_proc(pai[2]) in PROTEGIDOS:
                raiz = None  # pertence a algo protegido
                break
            raiz = pai[0]
        if raiz is None:
            continue
        r = info[raiz]
        n = _nome_proc(r[2])
        if n in nomes_anc or n in PROTEGIDOS:
            continue
        g = grupos.setdefault(n, [0, [], []])
        g[0] += mem
        g[1].append(pid)
        if pid == raiz:
            alvo = (r[5] if len(r) > 5 else "", r[6] if len(r) > 6 else "")
            if alvo not in g[2]:
                g[2].append(alvo)
    return dict(sorted(((n, tuple(g)) for n, g in grupos.items() if g[0] >= minimo), key=lambda x: -x[1][0]))


def _ollama_api(url, rota):
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/api/{rota}", timeout=5) as r:
            return json.load(r).get("models") or []
    except Exception:
        return []


def _mesmo_modelo(a, b):
    a, b = a.lower(), b.lower()
    return (a if ":" in a else a + ":latest") == (b if ":" in b else b + ":latest")


def folga_local(ex, mem=None):
    """Cabe o modelo do executor ollama agora? Devolve dict com cabe (True/False/None = nao deu para medir),
    precisa, livre, falta e detalhes."""
    url = ex.get("url") or os.environ.get("OLLAMA_API_URL", "http://127.0.0.1:11434")
    modelo = ex["modelo"]
    r = {"modelo": modelo, "cabe": None, "precisa": 0, "livre": 0, "falta": 0}
    if not garantir_ollama(url):
        r["motivo"] = "servidor ollama indisponivel"
        return r
    carregados = _ollama_api(url, "ps")
    if any(_mesmo_modelo(m.get("name", ""), modelo) for m in carregados):
        r.update(cabe=True, motivo="modelo ja carregado na memoria")
        return r
    tam = next((m.get("size", 0) for m in _ollama_api(url, "tags") if _mesmo_modelo(m.get("name", ""), modelo)), 0)
    if not tam:
        r["motivo"] = "modelo nao encontrado no 'ollama list'"
        return r
    ram_livre, ram_total, vram_livre, vram_total = mem or memoria_livre()
    outros = sum(m.get("size", 0) for m in carregados)  # o ollama descarrega modelos ociosos para abrir espaco
    precisa = int(tam * 1.15 + 1 * GB)  # pesos + cache de contexto e buffers
    livre = max(0, ram_livre - FOLGA_SO) + vram_livre + outros
    r.update(precisa=precisa, livre=livre, falta=max(0, precisa - livre), cabe=livre >= precisa,
             ram_livre=ram_livre, ram_total=ram_total, vram_livre=vram_livre, vram_total=vram_total,
             outros_modelos=outros)
    if r["cabe"] and vram_total and vram_livre + outros < tam * 0.5:
        r["aviso"] = "cabe, mas mais da metade fica na RAM: vai rodar lento"
    return r


def _gb(n):
    return f"{n / GB:.1f} GB"


def sugerir_apps(apps, falta):
    """Menor lista (dos maiores para os menores) que cobre o que falta. Devolve (nomes, memoria somada)."""
    nomes, soma = [], 0
    for nome, g in apps.items():
        if soma >= falta:
            break
        nomes.append(nome)
        soma += g[0]
    return nomes, soma


def relatorio_folga(eid, f, apps=None):
    linhas = [f"{eid} ({f['modelo']}): " + ("CABE" if f["cabe"] else "NAO CABE" if f["cabe"] is False else "sem medida")
              + (f" - {f['motivo']}" if f.get("motivo") else "")]
    if f.get("precisa"):
        linhas.append(f"  precisa ~{_gb(f['precisa'])}; livre {_gb(f['livre'])} = RAM {_gb(f['ram_livre'])} de "
                      f"{_gb(f['ram_total'])} (menos {_gb(FOLGA_SO)} do sistema) + VRAM {_gb(f['vram_livre'])} de "
                      f"{_gb(f['vram_total'])}" + (f" + outros modelos ollama {_gb(f['outros_modelos'])}" if f["outros_modelos"] else ""))
    if f.get("aviso"):
        linhas.append(f"  aviso: {f['aviso']}")
    if f["cabe"] is False and apps is not None:
        sugeridos, soma = sugerir_apps(apps, f["falta"])
        linhas.append(f"  falta {_gb(f['falta'])}. Apps que podem ser fechados (memoria em uso):")
        for nome, g in list(apps.items())[:15]:
            linhas.append(f"    {'*' if nome in sugeridos else ' '} {nome:28} {_gb(g[0]):>9}  ({len(g[1])} processo(s))")
        if soma < f["falta"]:
            linhas.append("  mesmo fechando tudo da lista nao sobra o suficiente: use outro executor (--pular-local)")
        else:
            linhas.append(f"  * = minimo sugerido (~{_gb(soma)}). Com autorizacao do usuario: "
                          f"maestro.py liberar --apps {','.join(sugeridos)}")
    return "\n".join(linhas)


def janela_liberar(f, apps, sugeridos, proximo, espera=JANELA_ESPERA):
    """Avisa numa janela que vai fechar os apps para o modelo local (zero token do orquestrador).
    Devolve 'fechar' (botao), 'tempo' (sem resposta: fecha tambem), 'nao' ou 'sem_janela'."""
    try:
        import tkinter as tk
        raiz = tk.Tk()
    except Exception:
        return "sem_janela"
    res = {"r": "tempo"}
    raiz.title("maestro: memoria para o modelo local")
    raiz.attributes("-topmost", True)
    raiz.resizable(False, False)
    caixa = tk.Frame(raiz, padx=16, pady=12)
    caixa.pack()
    tk.Label(caixa, justify="left", anchor="w", wraplength=460, text=(
        f"O modelo local {f['modelo']} precisa de ~{_gb(f['precisa'])}, mas so ha {_gb(f['livre'])} livres "
        f"(faltam {_gb(f['falta'])}). Para ele rodar, estes apps vao ser fechados e reabertos quando o "
        "trabalho terminar:")).pack(anchor="w")
    lista = tk.Frame(caixa, pady=8)
    lista.pack(anchor="w")
    for nome in sugeridos:
        g = apps[nome]
        tk.Label(lista, text=f"•  {nome}  ({_gb(g[0])})").pack(anchor="w")
    tk.Label(caixa, fg="#b00020", justify="left", wraplength=460,
             text="O que nao fechar sozinho e encerrado a forca: salve o que estiver aberto.").pack(anchor="w")
    relogio = tk.Label(caixa, fg="#555")
    relogio.pack(anchor="w", pady=(8, 0))
    botoes = tk.Frame(caixa, pady=10)
    botoes.pack(fill="x")

    def responder(r):
        res["r"] = r
        raiz.destroy()

    tk.Button(botoes, text="Fechar e rodar o modelo local", command=lambda: responder("fechar")).pack(side="left")
    tk.Button(botoes, text=f"Nao fechar (usar {proximo})", command=lambda: responder("nao")).pack(side="right")
    raiz.protocol("WM_DELETE_WINDOW", lambda: responder("nao"))
    fim = time.time() + espera

    def contar():
        resta = int(fim - time.time())
        if resta <= 0:
            return responder("tempo")
        relogio.config(text=f"Sem resposta em {resta}s: fecha os apps acima, roda o modelo e reabre tudo no fim.")
        raiz.after(1000, contar)

    contar()
    try:
        raiz.lift()
        raiz.focus_force()
        raiz.mainloop()
    except Exception:
        return "sem_janela"
    return res["r"]


def fechar_apps(pedidos, forcar=False):
    """Fecha os apps pedidos (so os liberaveis). Use SOMENTE com autorizacao do usuario.
    Devolve [{"nome", "alvos"}] do que foi fechado, para reabrir depois."""
    procs = processos()
    apps = apps_liberaveis(procs, minimo=0)
    antes = memoria_livre()
    fechados, recusados = [], []
    for nome in pedidos:
        if nome not in apps:
            recusados.append(nome)
            continue
        pids = apps[nome][1]
        if os.name == "nt":
            # sem /F o Windows pede para a janela fechar (o app pode salvar); /T ficaria de fora de proposito:
            # derrubaria filhos que talvez sejam o terminal desta sessao
            subprocess.run(["taskkill"] + [x for p in pids for x in ("/PID", str(p))], capture_output=True)
        else:
            import signal
            for p in pids:
                try:
                    os.kill(p, signal.SIGTERM)
                except OSError:
                    pass
        fechados.append((nome, pids, apps[nome][2]))
    for _ in range(16):  # ate ~8 s para os apps fecharem sozinhos
        vivos = {p[0] for p in processos()} if fechados else set()
        if not any(p in vivos for _, pids, _ in fechados for p in pids):
            break
        time.sleep(0.5)
    vivos = {p[0] for p in processos()} if fechados else set()
    for nome, pids, _ in fechados:
        resta = [p for p in pids if p in vivos]
        if resta and forcar:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F"] + [x for p in resta for x in ("/PID", str(p))], capture_output=True)
            else:
                import signal
                for p in resta:
                    try:
                        os.kill(p, signal.SIGKILL)
                    except OSError:
                        pass
            print(f"{nome}: forcado ({len(resta)} processo(s) nao fecharam sozinhos)")
        elif resta:
            print(f"{nome}: {len(resta)} processo(s) continuam abertos (talvez pedindo para salvar)")
        else:
            print(f"{nome}: fechado")
    for nome in recusados:
        print(f"{nome}: ignorado (nao esta aberto ou e protegido: sistema, terminal, sessoes de IA, ollama)")
    time.sleep(2 if fechados else 0)  # a memoria demora um pouco para voltar ao sistema
    depois = memoria_livre()
    print(f"RAM livre {_gb(antes[0])} -> {_gb(depois[0])}; VRAM livre {_gb(antes[2])} -> {_gb(depois[2])}")
    registrar({"evento": "liberar", "apps": [n for n, _, _ in fechados], "ram_ganha": depois[0] - antes[0],
               "vram_ganha": depois[2] - antes[2]})
    return [{"nome": n, "alvos": [list(a) for a in alvos]} for n, _, alvos in fechados]


# ---------- reabrir o que foi fechado ----------
REABRIR = os.path.join(CASA, "reabrir.json")  # apps fechados ainda nao reabertos (sobrevive a queda do maestro)
NAVEGADORES = {"chrome", "brave", "msedge", "vivaldi", "opera", "firefox"}


def guardar_para_reabrir(itens, dono):
    pend = ler_json(REABRIR, [])
    pend += [dict(i, dono=dono, quando=time.strftime("%Y-%m-%d %H:%M:%S")) for i in itens]
    gravar_json(REABRIR, pend)


def reabrir_apps(dono=None):
    """Reabre o que foi fechado (so os itens deste dono; None = todos) e tira da lista de pendentes."""
    pend = ler_json(REABRIR, [])
    meus = [i for i in pend if dono is None or i.get("dono") == dono]
    if not meus:
        return
    gravar_json(REABRIR, [i for i in pend if i not in meus])
    for item in meus:
        nome, ok = item["nome"], False
        for exe, cmd in item.get("alvos") or []:
            if not exe or not os.path.exists(exe):
                continue
            linha = cmd or f'"{exe}"'
            if nome in NAVEGADORES and "--restore-last-session" not in linha and nome != "firefox":
                linha += " --restore-last-session"  # volta com as abas que estavam abertas
            try:
                if os.name == "nt":
                    base = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
                    for flags in (base | 0x01000000, base):  # 0x01000000: sai do job do terminal, se ele deixar
                        try:
                            subprocess.Popen(linha, cwd=os.path.dirname(exe), creationflags=flags, close_fds=True,
                                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                            break
                        except OSError:
                            if flags == base:
                                raise
                else:
                    import shlex
                    subprocess.Popen(shlex.split(linha), cwd=os.path.dirname(exe) or None, start_new_session=True,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                ok = True
            except Exception:
                pass
        print(f"{nome}: reaberto" if ok else f"{nome}: nao consegui reabrir (abra manualmente)")
    registrar({"evento": "reabrir", "apps": [i["nome"] for i in meus]})


def soltar_modelo(url, modelo):
    """Tira o modelo da memoria na hora (senao o ollama o segura por 5 min e os apps reabertos ficam sem espaco)."""
    try:
        corpo = json.dumps({"model": modelo, "keep_alive": 0}).encode()
        req = urllib.request.Request(f"{url.rstrip('/')}/api/generate", data=corpo,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=30).read()
    except Exception:
        pass


def _ao_terminar(url, modelo, dono):
    soltar_modelo(url, modelo)
    reabrir_apps(dono)


def _sem_locais_que_nao_cabem(execs, cat, folgas, motivo):
    fica = [e for e in execs if folgas.get(e, {}).get("cabe") is not False]
    for e in execs:
        if e not in fica:
            print(f"pulando {e}: o PC nao tem memoria livre para {cat[e]['modelo']} ({motivo})")
    if not fica:
        sys.exit("nenhum executor sobrou depois de tirar os locais que nao cabem")
    return fica


def checar_locais(execs, cat, pular, janela=True):
    """Antes de rodar: se o primeiro executor da lista e ollama e o modelo nao cabe na memoria livre, o modelo
    local tem prioridade: uma janela avisa que os apps necessarios vao ser fechados. Botao 'fechar' ou sem
    resposta em JANELA_ESPERA s: fecha, roda e reabre tudo quando o maestro terminar. 'Nao fechar': segue a rota
    sem os locais que nao cabem. Sem janela possivel (ou --sem-janela): para com saida 5 e o orquestrador
    pergunta. --pular-local: nem pergunta. Devolve a lista de executores a usar."""
    if not execs or cat[execs[0]]["cli"] != "ollama":
        return execs
    mem = memoria_livre()
    folgas = {e: folga_local(cat[e], mem) for e in execs if cat[e]["cli"] == "ollama"}
    primeiro = folgas[execs[0]]
    if primeiro["cabe"] is not False:
        if primeiro.get("aviso"):
            print(f"AVISO: {execs[0]}: {primeiro['aviso']}")
        return execs
    if pular:
        return _sem_locais_que_nao_cabem(execs, cat, folgas, "--pular-local")
    apps = apps_liberaveis()
    sugeridos, soma = sugerir_apps(apps, primeiro["falta"])
    if soma < primeiro["falta"]:
        return _sem_locais_que_nao_cabem(execs, cat, folgas, "nem fechando todos os apps caberia")
    if janela and os.environ.get("MAESTRO_SEM_JANELA") != "1":
        proximo = next((e for e in execs[1:] if folgas.get(e, {}).get("cabe") is not False), "o proximo")
        resp = janela_liberar(primeiro, apps, sugeridos, proximo)
        registrar({"evento": "janela_local", "exec": execs[0], "resposta": resp, "apps": sugeridos})
        if resp in ("fechar", "tempo"):
            quem = "usuario autorizou" if resp == "fechar" else "sem resposta na janela, modelo local tem prioridade:"
            print(f"{execs[0]}: {quem} fechando {', '.join(sugeridos)} (reabre no fim)")
            ex = cat[execs[0]]
            url = ex.get("url") or os.environ.get("OLLAMA_API_URL", "http://127.0.0.1:11434")
            dono = f"{os.getpid()}-{secrets.token_hex(3)}"
            guardar_para_reabrir(fechar_apps(sugeridos, forcar=True), dono)
            primeiro = folga_local(ex)
            if primeiro["cabe"] is not False:
                import atexit
                atexit.register(_ao_terminar, url, ex["modelo"], dono)
                return execs
            print(f"{execs[0]}: ainda falta {_gb(primeiro['falta'])} depois de fechar; reabrindo os apps")
            reabrir_apps(dono)
            folgas[execs[0]] = primeiro
            return _sem_locais_que_nao_cabem(execs, cat, folgas, "nao coube nem depois de fechar")
        if resp == "nao":
            return _sem_locais_que_nao_cabem(execs, cat, folgas, "usuario preferiu nao fechar nada")
    print("PC SEM FOLGA PARA O MODELO LOCAL. Pergunte ao usuario antes de fechar qualquer coisa.")
    print(relatorio_folga(execs[0], primeiro, apps))
    outros = [e for e, f in folgas.items() if e != execs[0] and f["cabe"]]
    if outros:
        print(f"locais que cabem agora: {', '.join(outros)}")
    print("Saidas: autorizado -> 'maestro.py liberar --apps ...' e repita o comando (depois 'maestro.py reabrir'); "
          "nao autorizado -> repita com --pular-local (usa o proximo da rota).")
    sys.exit(5)


def cmd_recursos(a):
    cat = catalogo()
    ids = [a.exec] if a.exec else [e for e, ex in cat.items() if ex["cli"] == "ollama"]
    for e in ids:
        if e not in cat or cat[e]["cli"] != "ollama":
            sys.exit(f"{e} nao e um executor ollama do catalogo")
    mem, apps = memoria_livre(), None
    falhou = False
    for e in ids:
        f = folga_local(cat[e], mem)
        if f["cabe"] is False and apps is None:
            apps = apps_liberaveis()
        falhou = falhou or f["cabe"] is False
        print(relatorio_folga(e, f, apps))
    pend = ler_json(REABRIR, [])
    if pend:
        print(f"fechados pelo maestro e ainda nao reabertos: {', '.join(i['nome'] for i in pend)} (maestro.py reabrir)")
    sys.exit(5 if a.exec and falhou else 0)


def cmd_liberar(a):
    """Fecha os apps pedidos. So rode depois de o usuario autorizar a lista."""
    itens = fechar_apps([_nome_proc(x) for x in a.apps.split(",") if x.strip()], a.forcar)
    if itens:
        guardar_para_reabrir(itens, "manual")
        print("para reabrir depois: maestro.py reabrir")


def cmd_reabrir(a):
    if not ler_json(REABRIR, []):
        print("nada para reabrir")
    reabrir_apps(None)


def config_executor():
    """Arquivo de settings do executor claude: desliga plugins que capturam a sessao (ai-memory e afins)."""
    caminho = os.path.join(CASA, "executor-settings.json")
    plugins = {}
    try:
        with open(os.path.expanduser("~/.claude/settings.json"), encoding="utf-8") as f:
            plugins = {k: False for k in (json.load(f).get("enabledPlugins") or {})}
    except Exception:
        pass
    plugins.setdefault("ai-memory@ai-memory", False)
    conteudo = json.dumps({"enabledPlugins": plugins}, indent=2)
    try:
        with open(caminho, encoding="utf-8") as f:
            if f.read() == conteudo:
                return caminho
    except OSError:
        pass
    with open(caminho, "w", encoding="utf-8") as f:
        f.write(conteudo)
    return caminho


def montar(ex, prompt, sessao, timeout, leitura=False):
    cli, modelo = ex["cli"], ex["modelo"]
    exe = shutil.which("claude" if cli == "ollama" else cli)
    if not exe:
        sys.exit(f"{'claude' if cli == 'ollama' else cli} nao encontrado no PATH")
    env = dict(os.environ)
    if cli == "claude":
        for k in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT"):
            env.pop(k, None)
        # sem servidores MCP e sem skills: o executor nao precisa deles e eles inflam o contexto de cada chamada.
        # Sem plugins de memoria: senao o prompt do executor ("nao use a skill maestro") vira "habito do usuario"
        cmd = [exe, "-p", prompt, "--model", modelo, "--output-format", "json", "--dangerously-skip-permissions",
               "--strict-mcp-config", "--disable-slash-commands", "--settings", config_executor()]
        if leitura:
            cmd += ["--disallowedTools", "Edit", "Write", "NotebookEdit"]
        if ex.get("esforco"):
            cmd += ["--effort", ex["esforco"]]
        if sessao:
            cmd += ["--resume", sessao]
    elif cli == "ollama":
        for k in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT", "ANTHROPIC_AUTH_TOKEN"):
            env.pop(k, None)
        env["ANTHROPIC_BASE_URL"] = ex.get("url") or os.environ.get("OLLAMA_API_URL", "http://127.0.0.1:11434")
        env["ANTHROPIC_API_KEY"] = "ollama"
        # modelo local em loop gera texto ate o teto do Claude Code (32k); com teto menor ele falha antes e o maestro
        # passa para o proximo executor (catalogo: max_saida)
        env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(ex.get("max_saida", 16000))
        # O Claude Code nao conhece modelos do ollama e supoe 1M de contexto: nunca compacta. O ollama corta o
        # comeco da conversa sem avisar (onde estao as regras e a tarefa) e o modelo "esquece" o que fazer.
        # Com o contexto real (catalogo: contexto = num_ctx do modelo) ele compacta antes do corte.
        if ex.get("contexto"):
            env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = str(ex["contexto"])
            env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = str(int(ex["contexto"] * 0.8))
        cmd = [exe, "-p", prompt, "--model", modelo, "--output-format", "json", "--dangerously-skip-permissions",
               "--strict-mcp-config", "--disable-slash-commands", "--bare"]
        if leitura:
            cmd += ["--disallowedTools", "Edit", "Write", "NotebookEdit"]
        if sessao:
            cmd += ["--resume", sessao]
    elif cli == "agy":
        # --disable-slash-commands: sem expansao de skills/comandos no executor (agy >= 1.5); o modo padrao do
        # -p virou Auto no 1.5.7, por isso a autonomia vem explicita do --dangerously-skip-permissions
        # agy 1.5.12: "--mode plan has no effect while slash command expansion is disabled" -> na leitura o
        # --disable-slash-commands deixaria o executor escrever no projeto real; plan mode vale mais que as skills
        cmd = [exe, "-p", prompt, "--model", modelo, "--output-format", "json",
               "--dangerously-skip-permissions", "--print-timeout", f"{timeout}s"]
        if leitura:
            cmd += ["--mode", "plan"]
        else:
            cmd.append("--disable-slash-commands")
        if sessao:
            cmd += ["--conversation", sessao]
    elif cli == "opencode":
        cmd = [exe, "run", "--auto", "--agent", "leitor" if leitura else "worker", "-m", modelo, "--format", "json"]
        if sessao:
            cmd += ["--session", sessao]
        cmd.append(prompt)
    else:
        sys.exit(f"cli desconhecido no catalogo: {cli}")
    return cmd, env


def interpretar(cli, bruto):
    """Devolve (sessao, texto, tokens_in, tokens_out, erro)."""
    if cli in ("claude", "ollama", "agy"):
        dados = None
        for linha in reversed(bruto.strip().splitlines()):
            try:
                dados = json.loads(linha)
                break
            except ValueError:
                continue
        if not isinstance(dados, dict):
            return None, bruto[-1500:], 0, 0, "saida nao e JSON"
        u = dados.get("usage") or {}
        if cli in ("claude", "ollama"):
            tin = (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)
            erro = dados.get("result") if dados.get("is_error") else None
            return dados.get("session_id"), dados.get("result") or "", tin, u.get("output_tokens") or 0, erro
        erro = None if dados.get("status") == "SUCCESS" else (dados.get("error") or dados.get("status"))
        # agy separa o raciocinio em thinking_tokens; conta como saida do executor (e o que ele gastou)
        tout = (u.get("output_tokens") or 0) + (u.get("thinking_tokens") or 0)
        return dados.get("conversation_id"), dados.get("response") or "", u.get("input_tokens") or 0, tout, erro
    sessao, textos, erros, tin, tout = None, [], [], 0, 0
    for linha in bruto.splitlines():
        try:
            e = json.loads(linha)
        except ValueError:
            continue
        sessao = e.get("sessionID") or sessao
        parte = e.get("part") or {}
        if e.get("type") == "text":
            textos.append(parte.get("text", ""))
        elif e.get("type") == "step_finish":
            t = parte.get("tokens") or {}
            tin += t.get("input") or 0
            tout += t.get("output") or 0
        elif e.get("type") == "error":
            erros.append(json.dumps(e.get("error") or e, ensure_ascii=False)[:300])
    return sessao, "\n".join(textos), tin, tout, ("; ".join(erros[:2]) or None)


def relatorio(texto, limite=1500):
    i = texto.rfind("RESULTADO:")
    return (texto[i:] if i >= 0 else texto)[-limite:].strip() or "(sem texto final)"


def enxugar(texto, limite=40):
    """Saida longa de comando -> linhas com sinal de erro (com 1 de contexto), o inicio e o fim; linhas
    repetidas viram contagem. Saida curta volta identica. Devolve (texto, cortou)."""
    try:
        juntas = []
        for l in texto.strip().splitlines():
            if juntas and juntas[-1][0] == l:
                juntas[-1][1] += 1
            else:
                juntas.append([l, 1])
        linhas = [l[:300] if n == 1 else f"{l[:300]}  (x{n})" for l, n in juntas]
        if len(linhas) <= limite:
            return "\n".join(linhas), False
        manter = set(range(3)) | set(range(len(linhas) - 12, len(linhas)))
        for i, l in enumerate(linhas):
            if SINAL_RE.search(l):
                manter.update((i - 1, i, i + 1))
        idx = sorted(i for i in manter if 0 <= i < len(linhas))
        if len(idx) > limite:
            idx = idx[:limite - 12] + idx[-12:]
        out, ant = [], -1
        for i in idx:
            if i != ant + 1:
                out.append(f"  ... {i - ant - 1} linhas omitidas ...")
            out.append(linhas[i])
            ant = i
        return "\n".join(out), True
    except Exception:  # filtro nunca pode esconder a saida: na duvida, o fim bruto
        return "\n".join(texto.strip().splitlines()[-limite:]), True


def devolver(texto, limite, rotulo):
    """Resposta que cabe no limite volta inteira. A que nao cabe e gravada e volta inicio + fim + caminho."""
    texto = texto.strip()
    if len(texto) <= limite:
        return texto
    pasta = os.path.join(CASA, "respostas")
    os.makedirs(pasta, exist_ok=True)
    arq = os.path.join(pasta, f"{rotulo}-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}.md")
    with open(arq, "w", encoding="utf-8") as f:
        f.write(texto)
    ini, fim = limite * 2 // 3, limite // 3
    return f"{texto[:ini]}\n... {len(texto) - ini - fim} caracteres omitidos; resposta completa em {arq} ...\n{texto[-fim:]}"


def avisos_tarefa(texto, verificar):
    av = []
    if not verificar:
        av.append("sem --verificar: o 'aprovado' do executor nao prova nada")
    if not ARQ_RE.search(texto):
        av.append("a tarefa nao cita nenhum arquivo ou funcao: o executor vai gastar tokens procurando")
    if len(texto) < 200:
        av.append("tarefa com menos de 200 caracteres: se e pequena, faca voce mesmo; se nao e, falta especificacao")
    elif len(texto) >= 800 and not re.search(r"invariante|caminhos? de (falha|erro)|se (der|falhar|cair|houver) erro", texto, re.I):
        av.append("a tarefa nao diz o que acontece nos caminhos de falha (erro, timeout, cancelamento, entrada "
                  "invalida) nem as invariantes: e o que o validar mais reprova. Veja o modelo da secao 4 da skill")
    return av


def marca(texto):
    return hashlib.sha1(texto.strip().encode("utf-8", "replace")).hexdigest()[:10]


def duvida(texto):
    """Pergunta do executor que parou com ESCALAR:, ou None se ele entregou um RESULTADO depois."""
    m = ESCALAR_RE.search(texto or "")
    if not m or "RESULTADO:" in texto[m.end():]:
        return None
    return texto[m.end():].strip()[:4000] or None


# ---------- chamadas ----------

def perguntar(lista, cat, instrucao, texto, pasta, a):
    """Chamada sem edicao ao primeiro executor com cota.
    Devolve (eid, texto, tokens_in, tokens_out, erro, segundos) ou None se ninguem estava disponivel."""
    os.makedirs(CASA, exist_ok=True)
    arq = os.path.join(CASA, f"pedido-{secrets.token_hex(3)}.md")
    with open(arq, "w", encoding="utf-8") as f:
        f.write(texto)
    esg = esgotados()
    try:
        for eid in lista:
            if eid in esg:
                print(f"pulando {eid}: cota esgotada ate {esg[eid]}")
                continue
            ex = cat[eid]
            if ex["cli"] == "ollama" and not garantir_ollama(ex.get("url") or os.environ.get("OLLAMA_API_URL", "http://127.0.0.1:11434")):
                print(f"pulando {eid}: servidor ollama indisponivel; tentando o proximo")
                continue
            prompt = instrucao.replace("{arq}", arq).replace("{pasta}", pasta)
            cmd, env = montar(ex, prompt, None, a.timeout, leitura=True)
            onde = pasta
            if ex["cli"] == "agy":
                # o agy dispara os hooks do ai-memory com o cwd da sessao; rodando dentro de ~/.maestro/wt o
                # marcador vale e a leitura nao ensina habitos ao perfil. O projeto entra no workspace por --add-dir
                onde = os.path.join(CASA, "wt", "_leitura")
                os.makedirs(onde, exist_ok=True)
                garantir_marcador(os.path.dirname(onde))
                cmd += ["--add-dir", pasta]
            inicio = time.time()
            limite = min(a.timeout, int(ex.get("timeout", a.timeout)))
            url = ex.get("url") or os.environ.get("OLLAMA_API_URL", "http://127.0.0.1:11434")
            try:
                r = subprocess.run(cmd, cwd=onde, env=ambiente(env, onde, eid), stdin=subprocess.DEVNULL,
                                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   timeout=limite + 60)
            except subprocess.TimeoutExpired:
                if (ex.get("cota") in GRATIS or ex["cli"] == "ollama") and eid != lista[-1]:
                    print(f"{eid}: passou de {limite}s; tentando o proximo")
                    registrar({"evento": "falha_local", "exec": eid, "tipo": "ler", "motivo": f"passou de {limite}s"})
                    continue
                return eid, "", 0, 0, "TIMEOUT", round(time.time() - inicio)
            finally:
                if ex["cli"] == "ollama":
                    soltar_modelo(url, ex["modelo"])
            _, texto_r, tin, tout, erro = interpretar(ex["cli"], r.stdout)
            if (r.returncode != 0 or erro) and COTA_RE.search(f"{erro or ''} {r.stderr[-3000:]} {r.stdout[-3000:]}"):
                print(f"{eid}: cota esgotada (marcado ate {marcar_esgotado(eid, a.horas_cota)}); tentando o proximo")
                continue
            if (r.returncode != 0 or erro) and FORA_DO_AR_RE.search(f"{erro or ''} {r.stderr[-3000:]} {r.stdout[-3000:]}"):
                print(f"{eid}: servico fora do ar (pulado ate {marcar_esgotado(eid, FORA_DO_AR_H)}); tentando o proximo")
                continue
            if (ex.get("cota") in GRATIS or ex["cli"] == "ollama") and (r.returncode != 0 or erro or not texto_r.strip()):
                print(f"{eid}: falhou ({str(erro or 'resposta vazia')[:120]}); tentando o proximo")
                registrar({"evento": "falha_local", "exec": eid, "tipo": "ler", "motivo": str(erro or "resposta vazia")[:120]})
                continue
            return eid, texto_r, tin, tout, erro, round(time.time() - inicio)
        return None
    finally:
        try:
            os.remove(arq)
        except OSError:
            pass


def consultar(texto, n, cat, pasta, a, origem, sensivel=False, minimo=None):
    """Sobe a duvida para o modelo mais barato acima do nivel n. Mesmo retorno de perguntar()."""
    lista = acima(n, cat, sensivel, minimo)
    r = perguntar(lista, cat, CONSULTA, texto, pasta, a) if lista else None
    if r:
        registrar({"evento": "consulta", "exec": r[0], "de": origem, "status": "ERRO" if r[4] else "ok",
                   "segundos": r[5], "tokens_in": r[2], "tokens_out": r[3], "pedido_c": len(texto),
                   "resposta_c": len(r[1]), "h": marca(texto)})
    return r


def executar(j, execs, cat, a, passo=0):
    """Roda no worktree o primeiro executor com cota e devolve o que ele respondeu."""
    esg = esgotados()
    ultima_falha = None
    for eid in execs:
        if eid in esg:
            print(f"pulando {eid}: cota esgotada ate {esg[eid]}")
            continue
        ex = cat[eid]
        url = ex.get("url") or os.environ.get("OLLAMA_API_URL", "http://127.0.0.1:11434")
        if ex["cli"] == "ollama" and not garantir_ollama(url):
            print(f"pulando {eid}: servidor ollama indisponivel; tentando o proximo")
            continue
        sessao = j["sessao"] if j.get("exec_usado") == eid else None
        limite = min(a.timeout, int(ex.get("timeout", a.timeout)))  # catalogo: timeout proprio do executor
        cmd, env = montar(ex, REGRAS, sessao, limite)
        log = os.path.join(job_dir(j["job"]), f"log-r{j['rodada']}{f'-e{passo}' if passo else ''}-{eid}.txt")
        gratis = ex.get("cota") in GRATIS or ex["cli"] == "ollama"
        foto = foto_wt(j["wt"]) if gratis else None
        estourou = False
        inicio = time.time()
        try:
            r = subprocess.run(cmd, cwd=j["wt"], env=ambiente(env, j["wt"], eid), stdin=subprocess.DEVNULL,
                               capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=limite + 60)
            bruto, err, rc = r.stdout, r.stderr, r.returncode
        except subprocess.TimeoutExpired as t:
            bruto = t.stdout.decode("utf-8", "replace") if isinstance(t.stdout, bytes) else (t.stdout or "")
            err, rc, estourou = "", 124, True
        finally:
            if ex["cli"] == "ollama":
                soltar_modelo(url, ex["modelo"])  # nao segura 12 GB enquanto build/gradle rodam
        with open(log, "w", encoding="utf-8") as f:
            f.write(bruto + "\n--- stderr ---\n" + err)
        sessao_n, texto_r, tin, tout, erro = interpretar(ex["cli"], bruto)
        res = {"exec": eid, "sessao": sessao_n, "texto": texto_r, "tin": tin, "tout": tout, "erro": erro,
               "rc": rc, "estourou": estourou}
        if not estourou and (rc != 0 or erro) and COTA_RE.search(f"{erro or ''}\n{err[-3000:]}\n{bruto[-3000:]}"):
            ate = marcar_esgotado(eid, a.horas_cota)
            print(f"{eid}: cota esgotada (marcado ate {ate}); tentando o proximo")
            registrar({"evento": "cota", "exec": eid, "ate": ate})
            restaurar_wt(j["wt"], foto)
            continue
        # Executor gratis que nao entregou cede a vez NA HORA, sem gastar uma rodada de correcao: estourou o
        # tempo, deu erro, ou terminou sem mudar nada (perdeu a tarefa, so conversou). O worktree volta ao
        # que era antes dele e o fracasso entra no historico, de onde o ordenar_por_acerto aprende.
        if not estourou and (rc != 0 or erro) and FORA_DO_AR_RE.search(f"{erro or ''}\n{err[-3000:]}"):
            ate = marcar_esgotado(eid, FORA_DO_AR_H)
            print(f"{eid}: servico fora do ar (pulado ate {ate}, sem contar como derrota); tentando o proximo")
            registrar({"evento": "fora_do_ar", "exec": eid, "ate": ate})
            restaurar_wt(j["wt"], foto)
            ultima_falha = res
            continue
        motivo = None
        if gratis:
            if estourou:
                motivo = f"passou de {limite}s"
            elif rc != 0 or erro:
                motivo = str(erro or f"saiu com {rc}")[:120]
            elif (not duvida(texto_r) and not j.get("pode_nao_mudar") and len(execs) > 1
                  and foto_wt(j["wt"]) == foto):
                motivo = "terminou sem alterar nenhum arquivo"
        if motivo:
            print(f"{eid}: falhou ({motivo}); tentando o proximo")
            registrar({"evento": "falha_local", "exec": eid, "job": j["job"], "tipo": j.get("tipo"),
                       "motivo": motivo, "segundos": round(time.time() - inicio)})
            restaurar_wt(j["wt"], foto)
            ultima_falha = res
            continue
        return res
    if ultima_falha:  # todos falharam: devolve a ultima falha para a rodada reportar (nao deixa o job orfao)
        ultima_falha["erro"] = ultima_falha["erro"] or "todos os executores da lista falharam"
        return ultima_falha
    sys.exit("nenhum executor disponivel na lista (todos esgotados ou sem cota)")


# ---------- comandos ----------

def cmd_catalogo(a):
    cat, esg = catalogo(), esgotados()
    print(f"{'executor':18} {'nivel':5} {'cota':10} {'custo':7} {'flags':14} forte")
    for eid, ex in cat.items():
        flags = []
        if ex.get("poupar"):
            flags.append("poupar")
        if not ex.get("privado", True):
            flags.append("externo")
        if eid in esg:
            flags.append("ESGOTADO")
        print(f"{eid:18} {nivel(ex):<5} {ex['cota']:10} {ex['custo']:7} {','.join(flags):14} {ex.get('forte', '')}")
        if a.completo and ex.get("fraco"):
            print(f"{'':18} fraco: {ex['fraco']}")
    for eid, ate in esg.items():
        print(f"ESGOTADO: {eid} ate {ate}")
    print("rotas do --exec auto (por --tipo):")
    for tipo, lista in ler_catalogo().get("rotas", {}).items():
        print(f"  {tipo:12} " + " > ".join(e + ("(X)" if e in esg else "") for e in lista))


def correcao(j, execs, cat, pedido, auto):
    """Texto e executores da rodada de correcao. Se um executor gratis falhou na rodada anterior, ele nao
    tenta de novo: a correcao vai para o proximo da rota, que recebe a tarefa original porque nao tem a
    sessao de quem comecou. Executor pago segue na mesma sessao, que ja tem o contexto."""
    anterior, falhou = j.get("exec_usado"), j.get("falhou")
    troca = (auto and falhou and anterior in execs and len(execs) > 1
             and cat.get(anterior, {}).get("cota") in GRATIS)
    if not troca and anterior in execs:
        execs = [anterior] + [e for e in execs if e != anterior]  # quem tem a sessao vai primeiro
    # --exec com outro executor na frente: ele nao tem a sessao de quem comecou, precisa da tarefa original
    outro = bool(anterior) and bool(execs) and execs[0] != anterior
    if not (troca or outro):
        return execs, (f"# Correcao (rodada {j['rodada']})\n\nA tarefa anterior esta quase pronta. "
                       f"Corrija somente isto:\n\n{pedido}\n")
    if troca:
        execs = [e for e in execs if e != anterior] + [anterior]
        print(f"{anterior} falhou na rodada anterior ({falhou}); a correcao vai para o proximo da rota")
    falhou = falhou or "trabalho incompleto"
    try:
        with open(os.path.join(job_dir(j["job"]), "tarefa-r1.md"), encoding="utf-8") as f:
            original = f.read()
    except OSError:
        original = "(tarefa original indisponivel; deduza pelo trabalho parcial e pela correcao)"
    return execs, (f"{original}\n\n# Situacao (rodada {j['rodada']})\n\nOutro modelo comecou esta tarefa e o "
                   f"resultado falhou ({falhou}). O trabalho parcial dele esta neste worktree: aproveite o que "
                   f"estiver certo e termine a tarefa acima. Corrija tambem isto:\n\n{pedido}\n")


def cmd_run(a):
    cat = catalogo()
    if a.leitura:
        return cmd_leitura(a, cat)
    if a.continuar:
        j = carregar_job(a.continuar)
        if not os.path.isdir(j["wt"]):
            sys.exit("worktree do job nao existe mais")
        j["rodada"] += 1
        texto = a.texto or (open(a.tarefa, encoding="utf-8").read() if a.tarefa else None)
        if not texto:
            sys.exit("informe a correcao")
        execs = resolver(a.exec if a.exec != "auto" else j["exec"], j.get("tipo"), cat, a.sensivel or j.get("sensivel"))
        execs, texto = correcao(j, execs, cat, texto, a.exec == "auto")
        execs = checar_locais(execs, cat, a.pular_local, not a.sem_janela)
        if a.verificar:
            j["verificar"] = a.verificar
        avisos = []
    else:
        texto = open(a.tarefa, encoding="utf-8").read() if a.tarefa else a.texto
        if not texto:
            sys.exit("informe a tarefa (--tarefa arquivo.md ou texto)")
        execs = resolver(a.exec, a.tipo, cat, a.sensivel, meu_nivel(a.eu, cat))
        execs = checar_locais(execs, cat, a.pular_local, not a.sem_janela)
        avisos = avisos_tarefa(texto, a.verificar)
        for av in avisos:
            print("AVISO:", av)
        repo =git(os.path.abspath(a.dir), "rev-parse", "--show-toplevel").stdout.strip()
        if not repo:
            sys.exit("a pasta nao e um repositorio git; rode 'git init' e faca um commit inicial (o worktree isola o executor)")
        if git(repo, "rev-parse", "--verify", "HEAD").returncode != 0:
            sys.exit("repositorio sem nenhum commit; faca um commit inicial")
        licoes = ler_licoes(repo)
        if licoes:
            # o que as validacoes deste projeto ja reprovaram por falta de especificacao: o executor ja recebe
            texto += ("\n\n## Licoes de validacoes anteriores deste projeto (valem tambem para esta tarefa, "
                      "no que se aplicar)\n" + "\n".join(licoes) + "\n")
            print(f"licoes: {len(licoes)} do projeto anexadas a tarefa")
        job = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
        os.makedirs(job_dir(job), exist_ok=True)
        wt, base, aviso = criar_worktree(os.path.normpath(repo), job)
        if aviso:
            print("AVISO:", aviso)
        j = {"job": job, "repo": os.path.normpath(repo), "wt": wt, "base": base, "rodada": 1, "sessao": None,
             "exec": ",".join(execs), "tipo": a.tipo, "verificar": a.verificar, "sensivel": a.sensivel}
    eu = meu_nivel(a.eu, cat) or j.get("eu")
    j["eu"] = eu
    j["pode_nao_mudar"] = a.pode_nao_mudar or j.get("pode_nao_mudar", False)
    # Loop de verificacao: se o --verificar falha, o proprio maestro devolve a falha ao executor e roda de novo,
    # sem passar pelo orquestrador. Freios: --tentativas, RODADAS_MAX por job, mesma falha duas vezes seguidas
    # (sem progresso) e --orcamento de tokens do executor. O orquestrador so le o resumo final.
    rodadas, gasto, parou = [], 0, None
    while True:
        r = rodada(j, execs, texto, cat, a, eu)
        rodadas.append(r)
        gasto += r["tin"] + r["tout"]
        v = r["verif"]
        if not v or v["ok"] or r["status"] in ("TIMEOUT", "DUVIDA"):
            break
        if v.get("quebrada"):
            # Antes: o executor recebia "corrija o codigo" e gastava minutos ate escalar a duvida sobre o ambiente
            parou = (f"a VERIFICACAO nao conseguiu rodar (exit {v['rc']}: comando ou arquivo nao encontrado). Ela roda "
                     f"dentro do worktree {j['wt']}: use caminho absoluto para scripts fora do repo. Corrija com "
                     f"'run --continuar {j['job']} --verificar \"...\" \"rode a verificacao\"' ou confira o diff e junte")
            break
        v["assinatura"] = assinatura(v["saida"])
        if len(rodadas) > a.tentativas:
            parou = f"limite de {a.tentativas} correcao(oes) automatica(s)" if a.tentativas else None
        elif j["rodada"] >= RODADAS_MAX:
            parou = f"o job chegou a {RODADAS_MAX} rodadas"
        elif (len(rodadas) > 1 and rodadas[-2]["exec"] == r["exec"]
              and rodadas[-2]["verif"].get("assinatura") == v["assinatura"]):
            # so vale para o mesmo executor: outro executor com a mesma falha ainda e uma tentativa nova
            parou = "mesma falha da rodada anterior (sem progresso)"
        elif a.orcamento and gasto >= a.orcamento:
            parou = f"orcamento de {a.orcamento} tokens do executor atingido ({gasto})"
        if parou or len(rodadas) > a.tentativas:
            break
        registrar_rodada(j, r, auto=len(rodadas) > 1)
        print(f"rodada {j['rodada']}: " + ("executor nao alterou nenhum arquivo" if v.get("sem_mudanca") else
              f"verificacao FALHOU (exit {v['rc']})") + " -> correcao automatica")
        curto, _ = enxugar(v["saida"])
        pedido = (f"A verificacao `{j['verificar']}` falhou (exit {v['rc']}). Saida:\n\n```\n{curto}\n```\n\n"
                  "Corrija o codigo para que ela passe. Nao apague, pule nem enfraqueca testes ou a verificacao para "
                  "faze-la passar; se o teste contradiz a tarefa, responda ESCALAR: explicando.")
        j["rodada"] += 1
        execs, texto = correcao(j, execs, cat, pedido, a.exec == "auto" or bool(a.continuar))
        execs = checar_locais(execs, cat, a.pular_local)  # a correcao pode ter posto um local na frente

    r = rodadas[-1]
    usado, verif = r["exec"], r["verif"]
    ex = cat[usado]
    stat = mudancas(j, stat=True).strip()
    tin, tout = sum(x["tin"] for x in rodadas), sum(x["tout"] for x in rodadas)
    out = [f"job {j['job']} | {usado} ({ex['cota']}, {ex['custo']}) | rodada {j['rodada']} | {r['status']} | "
           f"{sum(x['seg'] for x in rodadas)}s",
           f"sessao: {j['sessao']} | tokens do executor: {tin}/{tout}"]
    if len(rodadas) > 1:
        out.append("correcao automatica: " + " -> ".join(
            f"r{x['rodada']} {x['exec']} {'OK' if x['verif'] and x['verif']['ok'] else x['status'] if not x['verif'] else 'FALHOU'}"
            for x in rodadas))
    if parou:
        out.append(f"correcao automatica parou: {parou}. Decida: corrigir voce, --continuar com outra instrucao ou descartar")
    if r["erro"]:
        out.append("erro: " + str(r["erro"])[:400])
    out.append("alteracoes:\n" + (stat[-1200:] if stat else "  nenhuma"))
    if verif is not None:
        out.append(f"verificacao: {'OK' if verif['ok'] else 'FALHOU'} (exit {verif['rc']})"
                   + (" - nenhum teste rodou; confira se o arquivo de teste existe e e encontrado" if verif.get("vazia") else "")
                   + (" - passou, mas o executor nao alterou nenhum arquivo" if verif.get("sem_mudanca") else ""))
        if not verif["ok"]:
            completo = os.path.join(job_dir(j["job"]), f"verif-r{j['rodada']}.txt")
            with open(completo, "w", encoding="utf-8") as f:
                f.write(verif["saida"])
            curto, cortou = enxugar(verif["saida"])
            out.append(curto)
            if cortou:
                out.append(f"(so as falhas; saida completa da verificacao: {completo})")
    out.append("--- relatorio do executor (nao confie sem conferir) ---")
    out.append(relatorio(r["texto_r"]))
    if r["pendente"]:
        out.append(f"--- o executor parou com uma duvida. Responda: maestro.py run --continuar {j['job']} \"sua decisao\"")
    out.append(f"--- worktree: {j['wt']}")
    out.append(f"proximo: maestro.py diff {j['job']} | juntar {j['job']} | run --continuar {j['job']} \"correcao\" | descartar {j['job']}")
    resumo = "\n".join(out)
    print(resumo)
    # so a rodada final volta ao orquestrador: as automaticas entram no historico com custo zero para ele
    registrar_rodada(j, r, auto=len(rodadas) > 1, devolvido_c=len(resumo), avisos=len(avisos), stat=stat)
    sys.exit(r["codigo"])


def assinatura(saida):
    """Impressao da falha sem o que muda a cada execucao (tempos, enderecos, numeros de linha, pastas
    temporarias). Duas rodadas seguidas com a mesma assinatura = o executor nao esta progredindo."""
    linhas = [l for l in saida.splitlines() if SINAL_RE.search(l)] or saida.strip().splitlines()[-5:]
    norm = re.sub(r"\d+(?:\.\d+)?\s*(?:ms|s|sec|seconds?)\b|0x[0-9a-fA-F]+|\bline \d+|:\d+\b|tmp[\w-]+", "#",
                  "\n".join(linhas))
    return marca(norm)


def rodada(j, execs, texto, cat, a, eu):
    """Uma rodada do job: executor, escaladas de duvida e verificacao. Nao imprime o resumo."""
    with open(os.path.join(j["wt"], TAREFA), "w", encoding="utf-8") as f:
        f.write(texto)
    with open(os.path.join(job_dir(j["job"]), f"tarefa-r{j['rodada']}.md"), "w", encoding="utf-8") as f:
        f.write(texto)
    tarefa_c = len(texto)
    inicio = time.time()
    res = executar(j, execs, cat, a)
    j["exec_usado"], j["sessao"] = res["exec"], res["sessao"] or j.get("sessao")
    tin, tout, consultas = res["tin"], res["tout"], []
    # O executor travou numa decisao. Se quem chamou e maior que ele, a pergunta volta para quem chamou
    # (saida 4). Senao sobe para um modelo acima e o mesmo executor continua, na mesma sessao, com a resposta.
    while len(consultas) < a.escalar and not res["estourou"] and eu and nivel(cat[res["exec"]]) >= eu:
        pergunta = duvida(res["texto"])
        if not pergunta:
            break
        c = consultar(f"# Tarefa em andamento\n\n{texto}\n\n# Duvida do executor ({res['exec']})\n\n{pergunta}\n",
                      nivel(cat[res["exec"]]), cat, j["wt"], a, res["exec"], sensivel=j.get("sensivel"))
        if not c or c[4] or not c[1].strip():
            print("escalada sem resposta:", "nenhum modelo acima disponivel" if not c else str(c[4] or "resposta vazia")[:200])
            break
        consultas.append(c[0])
        print(f"escalou: {res['exec']} -> {c[0]} ({c[5]}s, tokens da consulta {c[2]}/{c[3]})")
        texto += (f"\n\n# Resposta do modelo maior ({c[0]}) a sua duvida\n\n{c[1].strip()[-4000:]}\n\n"
                  "Siga essa decisao e termine a tarefa. Nao escale a mesma duvida de novo.\n")
        with open(os.path.join(j["wt"], TAREFA), "w", encoding="utf-8") as f:
            f.write(texto)
        res = executar(j, [res["exec"]], cat, a, passo=len(consultas))
        j["sessao"] = res["sessao"] or j.get("sessao")
        tin, tout = tin + res["tin"], tout + res["tout"]

    estourou, erro = res["estourou"], res["erro"]
    pendente = None if estourou else duvida(res["texto"])
    status = "TIMEOUT" if estourou else ("DUVIDA" if pendente else ("ok" if res["rc"] == 0 and not erro else "ERRO"))
    codigo = {"TIMEOUT": 2, "DUVIDA": 4, "ok": 0, "ERRO": 1}[status]
    verif = None
    if j.get("verificar") and status not in ("TIMEOUT", "DUVIDA"):
        try:
            cmdv = comando_verificar(j["verificar"])
            v = subprocess.run(cmdv, cwd=j["wt"], shell=isinstance(cmdv, str),
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                               capture_output=True, text=True, encoding="utf-8", errors="replace",
                               stdin=subprocess.DEVNULL, timeout=600)
            verif = {"ok": v.returncode == 0, "rc": v.returncode, "saida": v.stdout + v.stderr}
            if v.returncode in (126, 127) or VERIF_QUEBRADA_RE.search(verif["saida"][:2000]):
                # o proprio comando de verificacao nao rodou (nao achou o programa/script): nao e culpa do codigo
                verif["quebrada"] = True
            if verif["ok"] and VAZIA_RE.search(verif["saida"]):
                # unittest no Python < 3.12 sai com 0 quando nao acha teste; um executor que nao entrega teste passaria
                verif.update(ok=False, vazia=True)
        except subprocess.TimeoutExpired:
            verif = {"ok": False, "rc": 124, "saida": "verificacao estourou 600 s"}
        mudancas(j)  # limpa o que a verificacao possa ter deixado no indice
        if verif["ok"] and not j.get("pode_nao_mudar") and not mudancas(j, stat=True).strip():
            # o executor so conversou: os testes antigos passam, mas a tarefa nao foi feita
            verif.update(ok=False, rc=0, sem_mudanca=True, saida="FALHOU: o executor nao alterou nenhum arquivo; "
                         "a tarefa pede mudancas. Faca as alteracoes descritas em " + TAREFA)
        if not verif["ok"] and codigo == 0:
            codigo = 3
    j["falhou"] = status if status in ("ERRO", "TIMEOUT") else ("verificacao" if verif and not verif["ok"] else None)
    salvar_job(j)
    return {"exec": res["exec"], "rodada": j["rodada"], "texto_r": res["texto"], "erro": erro, "status": status,
            "codigo": codigo, "verif": verif, "pendente": pendente, "tin": tin, "tout": tout, "consultas": consultas,
            "seg": round(time.time() - inicio), "tarefa_c": tarefa_c}


def git_bash():
    """bash.exe do Git for Windows (o 'bash' do System32 e o WSL, outro sistema de arquivos)."""
    g = shutil.which("git")
    cands = [os.path.join(os.path.dirname(os.path.dirname(g)), "bin", "bash.exe")] if g else []
    cands += [r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files (x86)\Git\bin\bash.exe"]
    return next((c for c in cands if os.path.isfile(c)), None)


def comando_verificar(cmd):
    """No Windows o shell=True e o cmd.exe: 'bash script.sh | grep ...' falhava com 'bash nao reconhecido' e o
    executor parava com duvida sobre o ambiente. Verificacao que comeca com bash/sh roda inteira no Git Bash."""
    if os.name == "nt" and re.match(r"\s*(bash|sh)\s", cmd):
        gb = git_bash()
        if gb:
            return [gb, "-c", cmd]
    return cmd


def registrar_rodada(j, r, auto=False, devolvido_c=0, avisos=0, stat=None):
    """Um evento 'run' por rodada, para o 'sugerir' e o 'ganho' contarem cada tentativa. Rodada automatica
    nao custou nada ao orquestrador: tarefa_c e devolvido_c ficam 0."""
    stat = mudancas(j, stat=True) if stat is None else stat
    registrar({"evento": "run", "job": j["job"], "exec": r["exec"], "tipo": j.get("tipo"), "rodada": r["rodada"],
               "status": r["status"], "verificacao": None if r["verif"] is None else r["verif"]["ok"],
               "segundos": r["seg"], "tokens_in": r["tin"], "tokens_out": r["tout"], "repo": os.path.basename(j["repo"]),
               "consultas": r["consultas"], "auto": auto,
               "tarefa_c": 0 if auto else r["tarefa_c"],
               "devolvido_c": devolvido_c, "avisos": avisos,
               "diff_linhas": sum(int(n) for n in re.findall(r"(\d+) (?:insertion|deletion)", stat))})


def cmd_leitura(a, cat):
    texto = open(a.tarefa, encoding="utf-8").read() if a.tarefa else a.texto
    if not texto:
        sys.exit("informe o pedido")
    lista = checar_locais(resolver(a.exec, a.tipo or "ler", cat, a.sensivel, meu_nivel(a.eu, cat)), cat, a.pular_local, not a.sem_janela)
    r = perguntar(lista, cat, LEITURA, texto, os.path.abspath(a.dir), a)
    if not r:
        sys.exit("nenhum executor disponivel na lista")
    eid, texto_r, tin, tout, erro, seg = r
    if erro == "TIMEOUT":
        print(f"{eid}: TIMEOUT")
        sys.exit(2)
    resposta = devolver(texto_r, a.max_texto, "leitura")
    registrar({"evento": "leitura", "exec": eid, "tipo": a.tipo or "ler", "status": "ERRO" if erro else "ok",
               "segundos": seg, "tokens_in": tin, "tokens_out": tout, "pedido_c": len(texto),
               "resposta_c": len(resposta), "h": marca(texto)})
    print(f"leitura | {eid} ({cat[eid]['cota']}) | {'ERRO' if erro else 'ok'} | {seg}s | tokens {tin}/{tout}")
    if erro:
        print("erro:", str(erro)[:400])
    print(resposta)
    sys.exit(1 if erro else 0)


def cmd_consultar(a):
    cat = catalogo()
    n = meu_nivel(a.eu, cat)
    if n is None:
        sys.exit("informe --eu: o seu id no catalogo ou o seu nivel de 1 a 4")
    texto = open(a.tarefa, encoding="utf-8").read() if a.tarefa else a.texto
    if not texto:
        sys.exit("informe a duvida")
    if len(texto) < 150:
        print("AVISO: pergunta curta. Diga as opcoes que ve, o que ja verificou e quais arquivos olhar; "
              "sem isso o modelo caro le o projeto inteiro para adivinhar")
    r = consultar(texto, n, cat, os.path.abspath(a.dir), a, a.eu or os.environ.get("MAESTRO_EU"), a.sensivel, a.min)
    if not r:
        sys.exit(f"nao ha modelo disponivel acima do nivel {n}: decida voce mesmo ou pergunte ao usuario")
    eid, texto_r, tin, tout, erro, seg = r
    if erro == "TIMEOUT":
        print(f"{eid}: TIMEOUT")
        sys.exit(2)
    ex = cat[eid]
    print(f"consulta | {eid} (nivel {nivel(ex)}, {ex['cota']}) | {'ERRO' if erro else 'ok'} | {seg}s | tokens {tin}/{tout}")
    if erro:
        print("erro:", str(erro)[:400])
    print(devolver(texto_r, a.max_texto, "consulta"))
    sys.exit(1 if erro else 0)


LICOES_MAX = 40  # linhas guardadas por projeto; o run anexa so as ultimas LICOES_RUN
LICOES_RUN = 15
LICOES_RE = re.compile(r"^[\s*#]*LI[CÇ][OÕ]ES[\s*]*:[\s*]*(.*)$", re.I)
SECAO_RE = re.compile(r"^[\s*#]*[A-ZÇÃÕÁÉÍÓÚ][A-ZÇÃÕÁÉÍÓÚ ]{3,}[\s*]*:")  # titulo de secao, nunca linha '- '


def licoes_arq(repo):
    return os.path.join(CASA, "licoes", os.path.basename(os.path.normpath(repo)) + ".md")


def ler_licoes(repo, n=LICOES_RUN):
    try:
        with open(licoes_arq(repo), encoding="utf-8") as f:
            linhas = [l.rstrip() for l in f if l.startswith("- ")]
    except OSError:
        return []
    return linhas[-n:] if n else linhas


def extrair_licoes(texto):
    """Linhas '- ...' da secao LICOES da resposta do revisor (ate a proxima secao em maiusculas)."""
    out, dentro = [], False
    for l in (texto or "").splitlines():
        m = LICOES_RE.match(l)
        if m:
            dentro = True
            resto = m.group(1).strip()
            if resto.startswith("- "):
                out.append(resto)
            continue
        if dentro and SECAO_RE.match(l):
            break
        if dentro and l.strip().startswith(("- ", "* ")):
            out.append("- " + l.strip()[2:].strip())
    return [l for l in out if len(l) > 6 and "nenhuma" not in l.lower()[:12]]


def gravar_licoes(repo, novas):
    """Junta as licoes novas as do projeto, sem repetir, e guarda as ultimas LICOES_MAX. Devolve quantas entraram."""
    antigas = ler_licoes(repo, 0)
    vistas = {re.sub(r"\W+", " ", l.lower()).strip() for l in antigas}
    entram = []
    for l in novas:
        k = re.sub(r"\W+", " ", l.lower()).strip()
        if k not in vistas:
            vistas.add(k)
            entram.append(l)
    if entram:
        os.makedirs(os.path.dirname(licoes_arq(repo)), exist_ok=True)
        with open(licoes_arq(repo), "w", encoding="utf-8") as f:
            f.write("# Licoes das validacoes deste projeto (o maestro anexa as ultimas a cada tarefa nova)\n\n"
                    + "\n".join((antigas + entram)[-LICOES_MAX:]) + "\n")
    return len(entram)


def validacao_anterior(repo, horas=12):
    """A validacao mais recente do mesmo projeto nas ultimas `horas` que chegou a um veredito (uma que caiu por
    timeout ou erro e pulada): (id, resposta, meta) ou None."""
    pasta = os.path.join(CASA, "validacoes")
    try:
        ids = sorted(os.listdir(pasta), reverse=True)
    except OSError:
        return None
    for vid in ids:
        meta = ler_json(os.path.join(pasta, vid, "meta.json"), None)
        resp = os.path.join(pasta, vid, "resposta.md")
        if not meta or os.path.normcase(meta.get("repo", "")) != os.path.normcase(repo) or not os.path.isfile(resp):
            continue
        if time.time() - os.path.getmtime(resp) > horas * 3600:
            return None
        with open(resp, encoding="utf-8") as f:
            texto = f.read().strip()
        if VEREDITO_RE.search(texto):
            return vid, texto, meta
    return None


def foto_arvore(pasta):
    """Hash de uma arvore git com o estado atual da pasta (commitado, modificado e novo nao ignorado), feita
    num indice temporario: nao toca o indice nem o working tree do usuario. None se falhar."""
    os.makedirs(os.path.join(CASA, "validacoes"), exist_ok=True)
    idx = os.path.join(CASA, "validacoes", f".idx-{secrets.token_hex(4)}")
    env = {**os.environ, "GIT_INDEX_FILE": idx}
    try:
        for args in (["read-tree", "HEAD"], ["add", "-A", "--", ".", *LIXO], ["write-tree"]):
            r = subprocess.run(["git", *args], cwd=pasta, env=env, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
            if r.returncode != 0:
                return None
        return r.stdout.strip() or None
    finally:
        try:
            os.remove(idx)
        except OSError:
            pass


def regua(texto):
    """Veredito que a regua do VALIDA manda dar, pelas etiquetas da secao PROBLEMAS: REPROVADO com [bloqueia],
    APROVADO COM RESSALVAS com [importante], APROVADO sem nenhum dos dois. None se a secao nao foi achada."""
    m = re.search(r"^[\s*#]*PROBLEMAS[\s*]*:(.*?)(?=^[\s*#]*(?:FORA DO VEREDITO|LI[CÇ][OÕ]ES|CONFERIDO)[\s*]*:|\Z)",
                  texto or "", re.S | re.M | re.I)
    if not m:
        return None
    sec = m.group(1).lower()
    if "[bloqueia" in sec:
        return "REPROVADO"
    if "[importante" in sec:
        return "APROVADO COM RESSALVAS"
    return "APROVADO"


def cmd_licoes(a):
    repo = git(os.path.abspath(a.dir), "rev-parse", "--show-toplevel").stdout.strip() or os.path.abspath(a.dir)
    linhas = ler_licoes(repo, 0)
    print(f"licoes de {os.path.basename(os.path.normpath(repo))} ({licoes_arq(repo)}):")
    print("\n".join(linhas) if linhas else "  nenhuma ainda")


def cmd_validar(a):
    """Um modelo de topo (Opus ou Fable, esforco medio) revisa o trabalho feito por qualquer modelo e da o
    veredito. Fonte do trabalho: um job do maestro (--job) ou as mudancas do projeto desde --base (padrao HEAD,
    ou seja, o que ainda nao foi commitado, mais arquivos novos)."""
    pedido = open(a.tarefa, encoding="utf-8").read() if a.tarefa else a.texto
    if not pedido or len(pedido.strip()) < 20:
        sys.exit("informe o que foi pedido (texto ou --tarefa arq.md), com as palavras do usuario: "
                 "sem isso o revisor nao tem contra o que julgar")
    if a.job:
        j = carregar_job(a.job)
        if not os.path.isdir(j["wt"]):
            sys.exit("worktree do job nao existe mais (ja juntado? valide o projeto sem --job)")
        pasta, diff, stat, novos = j["wt"], mudancas(j), mudancas(j, stat=True), []
        repo = j["repo"]
        try:
            with open(os.path.join(job_dir(j["job"]), "tarefa-r1.md"), encoding="utf-8") as f:
                pedido += "\n\n## Especificacao entregue ao executor\n\n" + f.read()
        except OSError:
            pass
    else:
        pasta = git(os.path.abspath(a.dir), "rev-parse", "--show-toplevel").stdout.strip()
        if not pasta:
            sys.exit("a pasta nao e um repositorio git: o revisor precisa de um diff para julgar")
        pasta = repo = os.path.normpath(pasta)
        if git(pasta, "rev-parse", "--verify", a.base).returncode != 0:
            sys.exit(f"--base {a.base} nao existe neste repositorio")
        diff = git(pasta, "diff", a.base, "--", ".", *LIXO).stdout
        stat = git(pasta, "diff", "--stat", a.base, "--", ".", *LIXO).stdout
        novos = [l for l in git(pasta, "ls-files", "--others", "--exclude-standard").stdout.splitlines() if l.strip()]
    if not diff.strip() and not novos and a.job:
        sys.exit(f"o job {a.job} nao tem nenhuma alteracao no worktree: nao ha o que validar")
    if not diff.strip() and not novos:
        sys.exit(f"nada mudou desde {a.base}. Se o trabalho ja foi commitado, passe a base de antes dele "
                 "(ex.: --base HEAD~1 ou --base main)")
    vid = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
    pasta_v = os.path.join(CASA, "validacoes", vid)
    os.makedirs(pasta_v, exist_ok=True)
    arq_diff = os.path.join(pasta_v, "diff.patch")
    with open(arq_diff, "w", encoding="utf-8", newline="\n") as f:
        f.write(diff)
    arvore = foto_arvore(pasta)
    ant = None if a.anterior == "nenhuma" else validacao_anterior(repo)
    with open(os.path.join(pasta_v, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({"repo": repo, "base": None if a.job else a.base, "job": a.job, "arvore": arvore}, f)
    partes = [f"# O que foi pedido\n\n{pedido.strip()}\n"]
    if ant:
        partes.append(f"# Validacao anterior ({ant[0]}), desta mesma sessao de trabalho\n\n"
                      "Confira primeiro se cada problema dela foi corrigido. Item que o pedido acima diz que o usuario "
                      "decidiu nao corrigir nao volta como problema. Se o pedido acima for outro trabalho, ignore esta "
                      f"secao.\n\n{ant[1][-6000:]}\n")
    n_diff = len(diff.splitlines())
    partes += [f"# O que mudou\n\nResumo:\n{stat.strip() or '(so arquivos novos)'}\n\n"
              f"Diff completo ({n_diff} linhas): {arq_diff}\nLeia o diff inteiro antes de julgar."]
    inter = None
    if ant and arvore and ant[2].get("arvore"):
        r_i = git(pasta, "diff", ant[2]["arvore"], arvore, "--", ".", *LIXO)
        if r_i.returncode == 0:
            inter = r_i.stdout
            arq_i = os.path.join(pasta_v, "diff-desde-anterior.patch")
            with open(arq_i, "w", encoding="utf-8", newline="\n") as f:
                f.write(inter)
            partes.append("# O que mudou desde a validacao anterior\n\n" + (
                f"{len(inter.splitlines())} linhas: {arq_i}\nComece por ele: e a correcao. Leia-o inteiro e confira "
                "se a correcao trouxe problema novo nos caminhos que ela toca."
                if inter.strip() else "Nada mudou desde a validacao anterior: nenhuma correcao foi feita."))
    if novos:
        partes.append("Arquivos novos ainda nao rastreados pelo git (leia na pasta do projeto):\n"
                      + "\n".join(f"- {n}" for n in novos[:200]))
    if a.verificar:
        try:
            v = subprocess.run(a.verificar, cwd=pasta, shell=True, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", stdin=subprocess.DEVNULL, timeout=600)
            curto, _ = enxugar(v.stdout + v.stderr)
            partes.append(f"# Verificacao ja rodada pelo maestro\n\n`{a.verificar}` -> exit {v.returncode}\n```\n{curto}\n```")
        except subprocess.TimeoutExpired:
            partes.append(f"# Verificacao ja rodada pelo maestro\n\n`{a.verificar}` estourou 600 s")
    texto = "\n\n".join(partes) + "\n"
    with open(os.path.join(pasta_v, "pedido.md"), "w", encoding="utf-8") as f:
        f.write(texto)
    val = {**VALIDADORES[a.modelo], "esforco": a.esforco}
    eid = f"validar-{a.modelo}"
    if n_diff > 4000 and a.esforco == "medium" and not (inter and inter.strip()):
        print(f"AVISO: diff de {n_diff} linhas; em esforco medio o revisor le por amostragem e cada rodada pode "
              "achar coisa nova. Para uma revisao completa: --esforco high, ou valide por partes (--base)")
    print(f"validando com {val['modelo']} (esforco {val['esforco']}) em {pasta} ...", flush=True)
    r = perguntar([eid], {eid: val}, VALIDA, texto, pasta, a)
    if not r:
        sys.exit(f"{eid} indisponivel (cota esgotada?). Tente o outro: --modelo {'fable' if a.modelo == 'opus' else 'opus'}")
    _, texto_r, tin, tout, erro, seg = r
    m = VEREDITO_RE.search(texto_r or "")
    veredito = dito = m.group(1).upper() if m else None
    if veredito and not erro:
        # o veredito segue a regua pelas etiquetas, nao a impressao do revisor
        veredito = regua(texto_r) or veredito
    with open(os.path.join(pasta_v, "resposta.md"), "w", encoding="utf-8") as f:
        f.write(texto_r or "")
    novas = gravar_licoes(repo, extrair_licoes(texto_r)) if veredito else 0
    registrar({"evento": "validacao", "exec": eid, "status": "ERRO" if erro else "ok", "veredito": veredito,
               "veredito_dito": dito, "esforco": a.esforco, "revalidacao": bool(ant),
               "segundos": seg, "tokens_in": tin, "tokens_out": tout, "pedido_c": len(texto),
               "resposta_c": len(texto_r or ""), "job": a.job, "repo": os.path.basename(pasta)})
    print(f"validacao | {val['modelo']} | {'TIMEOUT' if erro == 'TIMEOUT' else 'ERRO' if erro else 'ok'} "
          f"| {seg}s | tokens {tin}/{tout} | arquivos em {pasta_v}")
    if erro:
        print("erro:", str(erro)[:400])
    print(devolver(texto_r or "(sem resposta)", a.max_texto, "validacao"))
    if veredito and veredito != dito:
        print(f"VEREDITO PELA REGUA: {veredito} (o revisor escreveu {dito}, mas as etiquetas dos PROBLEMAS mandam "
              f"{veredito}: [bloqueia] reprova, [importante] da ressalva, o resto nao pesa)")
    if ant:
        print(f"(revalidacao: a validacao anterior {ant[0]} foi junto para o revisor"
              + (f"; {len(inter.splitlines())} linhas mudaram desde ela)" if inter is not None else ")"))
    if novas:
        print(f"licoes: {novas} nova(s) guardada(s) em {licoes_arq(repo)}; entram sozinhas nas proximas tarefas "
              "do maestro neste projeto (veja com: maestro.py licoes)")
    if erro or not veredito:
        if not erro:
            print("AVISO: a resposta nao trouxe a linha VEREDITO; trate como nao validado")
        sys.exit(2 if erro == "TIMEOUT" else 1)
    sys.exit(3 if veredito == "REPROVADO" else 0)


def cmd_rota(a):
    cat, esg = catalogo(), esgotados()
    n = meu_nivel(a.eu, cat)
    if n is None:
        sys.exit("informe --eu: o seu id no catalogo ou o seu nivel de 1 a 4")
    marca = lambda ids: " > ".join(f"{e}[{nivel(cat[e])}]" + ("(X)" if e in esg else "") for e in ids) or "ninguem"
    rotas = ler_catalogo().get("rotas", {})
    print(f"voce: nivel {n}")
    for tipo in ([a.tipo] if a.tipo else [t for t in rotas if t != "dificil"]):
        ids = [e for e in rotas.get(tipo, []) if e in cat and nivel(cat[e]) <= n
               and (cat[e].get("privado", True) or not a.sensivel)]
        print(f"  descer/igual  run --tipo {tipo:12} {marca(ordenar_por_acerto(ids, tipo))}")
    print(f"  subir         consultar              {marca(acima(n, cat, a.sensivel))}")


def cmd_diff(a):
    j = carregar_job(a.job)
    d = mudancas(j, stat=a.stat, caminho=a.arquivo)
    linhas = d.splitlines()
    if not a.stat and a.max and len(linhas) > a.max:
        print("\n".join(linhas[:a.max]))
        print(f"... diff cortado em {a.max} de {len(linhas)} linhas. Leia por arquivo: maestro.py diff {a.job} "
              f"--arquivo <caminho> (lista em --stat), ou inteiro com --max 0")
    else:
        print(d or "sem alteracoes")


def cmd_juntar(a):
    j = carregar_job(a.job)
    patch = mudancas(j, binario=True)
    if not patch.strip():
        print("nada para juntar")
    else:
        caminho = os.path.join(job_dir(j["job"]), "final.patch")
        with open(caminho, "w", encoding="utf-8", newline="\n") as f:
            f.write(patch)
        r = git(j["repo"], "apply", "--whitespace=nowarn", caminho)
        if r.returncode != 0:  # projeto real mudou nesse meio tempo: tenta mesclar
            r = git(j["repo"], "apply", "--3way", "--whitespace=nowarn", caminho)
        if r.returncode != 0:
            # --3way recusa arquivo com mudanca fora do indice (o orquestrador mexeu no mesmo arquivo enquanto o
            # job rodava). Com 1 linha de contexto, hunks em trechos diferentes do arquivo ainda entram
            r2 = git(j["repo"], "apply", "-C1", "--whitespace=nowarn", caminho)
            if r2.returncode == 0:
                arqs = [l.split("\t")[-1] for l in git(j["repo"], "apply", "--numstat", caminho).stdout.splitlines() if l]
                print("AVISO: aplicado com contexto reduzido (o arquivo mudou no projeto real); um trecho pode ter "
                      "caido no lugar errado. Confira o diff destes arquivos: " + ", ".join(arqs))
            r = r2 if r2.returncode == 0 else r
        if r.returncode != 0:
            sys.exit(f"git apply falhou; worktree mantido. Patch: {caminho}\n{r.stderr.strip()[:800]}")
        print("aplicado no projeto:\n" + git(j["repo"], "diff", "--stat").stdout[-1500:])
    remover_worktree(j)
    registrar({"evento": "juntar", "job": j["job"], "exec": j.get("exec_usado"), "tipo": j.get("tipo"), "rodadas": j["rodada"]})
    print("worktree removido. Nada foi commitado.")


def cmd_descartar(a):
    j = carregar_job(a.job)
    remover_worktree(j)
    registrar({"evento": "descartar", "job": j["job"], "exec": j.get("exec_usado"), "tipo": j.get("tipo"), "rodadas": j["rodada"]})
    print("worktree descartado")


def cmd_jobs(a):
    raiz = os.path.join(CASA, "jobs")
    for job in sorted(os.listdir(raiz)) if os.path.isdir(raiz) else []:
        j = ler_json(os.path.join(raiz, job, "job.json"), None)
        if j and os.path.isdir(j["wt"]):
            print(f"{job} | {j.get('exec_usado')} | rodada {j['rodada']} | {os.path.basename(j['repo'])} | {j.get('tipo') or ''}")


def cmd_cota(a):
    if a.liberar:
        est = ler_json(ESTADO, {})
        est.get("esgotado", {}).pop(a.id, None)
        gravar_json(ESTADO, est)
        print(f"{a.id} liberado")
    else:
        print(f"{a.id} esgotado ate {marcar_esgotado(a.id, a.horas)}")


def cmd_sugerir(a):
    runs = collections.defaultdict(list)
    finais = {}
    try:
        with open(HISTORICO, encoding="utf-8") as f:
            for linha in f:
                e = json.loads(linha)
                if a.tipo and e.get("tipo") != a.tipo:
                    continue
                if e["evento"] == "run":
                    runs[(e.get("tipo") or "-", e["exec"])].append(e)
                elif e["evento"] in ("juntar", "descartar"):
                    finais[e["job"]] = (e["evento"], e.get("rodadas", 1))
    except OSError:
        pass
    if not runs:
        print("sem historico ainda")
        return
    print(f"{'tipo':12} {'executor':18} {'rodadas':>7} {'verif ok':>8} {'1a ok':>6} {'juntado':>8} {'seg med':>7}")
    for (tipo, eid), lst in sorted(runs.items()):
        ver = [e["verificacao"] for e in lst if e.get("verificacao") is not None]
        primeira = [e["verificacao"] for e in lst if e["rodada"] == 1 and e.get("verificacao") is not None]
        jobs = {e["job"] for e in lst}
        junt = [finais[jb][0] == "juntar" for jb in jobs if jb in finais]
        pct = lambda xs: f"{100 * sum(xs) // len(xs)}%" if xs else "-"
        print(f"{tipo:12} {eid:18} {len(lst):>7} {pct(ver):>8} {pct(primeira):>6} {pct(junt):>8} {sum(e['segundos'] for e in lst) // len(lst):>7}")
    # O que o maestro aprendeu: tentativas por (job, executor), contando quem falhou e cedeu a vez
    tents = _tentativas()
    rotas = ler_catalogo().get("rotas", {})
    print(f"\nAPRENDIZADO (ultimos {ACERTO_DIAS} dias; tentativa = executor num job; ganha quem entregou o que foi juntado)")
    for tipo, lista in rotas.items():
        if a.tipo and tipo != a.tipo:
            continue
        placar = acerto(tipo, tentativas=tents)
        efetiva = ordenar_por_acerto(lista, tipo, silencioso=True)
        print(f"  {tipo:12} ordem efetiva: " + " > ".join(
            f"{e}({placar[e][0]}/{placar[e][1]})" if placar.get(e, [0, 0])[1] else f"{e}(-)" for e in efetiva))
        if efetiva != lista:
            print(f"  {'':12} catalogo:      " + " > ".join(lista))


def economia(ev, cat, periodo):
    """Resumo do que saiu do orquestrador: tokens que os executores gastaram em trabalho aproveitado,
    menos o que o orquestrador pagou para mandar a tarefa e ler a resposta (caracteres/4).
    Consultas acima nao entram: sao gasto extra, nao economia."""
    finais = {e["job"]: e["evento"] for e in ev if e.get("evento") in ("juntar", "descartar")}
    fora = perdido = custo = gratis = 0
    for e in ev:
        if e.get("evento") not in ("run", "leitura"):
            continue
        t = (e.get("tokens_in") or 0) + (e.get("tokens_out") or 0)
        custo += ((e.get("tarefa_c") or e.get("pedido_c") or 0) + (e.get("devolvido_c", e.get("resposta_c")) or 0)) // 4
        if e.get("evento") == "run" and finais.get(e.get("job")) == "descartar":
            perdido += t
            continue
        fora += t
        if cat.get(e.get("exec"), {}).get("cota") in ("local", "omniroute"):
            gratis += t
    liquido = fora - custo
    print(f"ECONOMIA ({periodo})")
    print(f"  tokens que nao passaram pelo orquestrador: {liquido:,}".replace(",", "."))
    print(f"    = {fora:,} feitos pelos executores em trabalho aproveitado".replace(",", ".")
          + f" ({gratis:,} em modelo gratis: local/omniroute)".replace(",", "."))
    print(f"    - {custo:,} que o orquestrador gastou mandando tarefas e lendo respostas".replace(",", "."))
    if perdido:
        print(f"  fora da conta: {perdido:,} tokens de executor em jobs descartados".replace(",", "."))
    print("  teto: o executor rele o contexto a cada turno; o orquestrador faria o mesmo trabalho com menos tokens que isso.")
    print()


def indicadores(ev):
    runs = [e for e in ev if e.get("evento") == "run"]
    jobs_run = {e["job"] for e in runs if e.get("job")}
    n_jobs = len(jobs_run)

    p1 = [e for e in runs if e.get("rodada") == 1 and e.get("verificacao") is not None]
    primeira_ok = round(100 * sum(1 for e in p1 if e.get("verificacao")) / len(p1)) if p1 else None

    max_rodadas = {}
    for e in runs:
        jb = e.get("job")
        if jb:
            r = e.get("rodada", 1)
            if jb not in max_rodadas or r > max_rodadas[jb]:
                max_rodadas[jb] = r
    rodadas_media = round(sum(max_rodadas.values()) / len(max_rodadas), 1) if max_rodadas else None

    finais = {e["job"]: e["evento"] for e in ev if e.get("evento") in ("juntar", "descartar") and e.get("job")}
    total_finais = len(finais)
    n_juntados = sum(1 for st in finais.values() if st == "juntar")
    juntado = round(100 * n_juntados / total_finais) if total_finais else None

    soma_custo = 0
    for e in ev:
        if e.get("evento") in ("run", "leitura"):
            entrada = e.get("tarefa_c") or e.get("pedido_c") or 0
            saida = e.get("devolvido_c") or e.get("resposta_c") or 0
            soma_custo += (entrada + saida) // 4
    custo_por_juntado = soma_custo // n_juntados if n_juntados else None

    return {
        "jobs": n_jobs,
        "primeira_ok": primeira_ok,
        "rodadas_media": rodadas_media,
        "juntado": juntado,
        "custo_por_juntado": custo_por_juntado,
    }


def cmd_ganho(a):
    if getattr(a, "comparar", False) and a.dias <= 0:
        print("--comparar precisa de --dias maior que 0")
    agora = time.time()
    limite_atual = "" if a.dias <= 0 else time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(agora - a.dias * 86400))
    limite_anterior = "" if (a.dias <= 0 or not getattr(a, "comparar", False)) else time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(agora - 2 * a.dias * 86400))
    desde = limite_anterior if limite_anterior else limite_atual
    ev_todos = []
    try:
        with open(HISTORICO, encoding="utf-8") as f:
            for linha in f:
                try:
                    e = json.loads(linha)
                except ValueError:
                    continue
                d = e.get("data", "")
                if desde and d < desde:
                    continue
                ev_todos.append(e)
    except OSError:
        pass
    if a.dias > 0 and getattr(a, "comparar", False):
        ev_anterior = [e for e in ev_todos if limite_anterior <= e.get("data", "") < limite_atual]
        ev_atual = [e for e in ev_todos if e.get("data", "") >= limite_atual]
    else:
        ev_anterior = []
        ev_atual = [e for e in ev_todos if e.get("data", "") >= limite_atual] if limite_atual else ev_todos
    ev = ev_atual
    cham = [e for e in ev if e.get("evento") in ("run", "leitura", "consulta", "validacao")]
    if not cham:
        print("sem historico no periodo")
        return
    cat = catalogo()
    economia(ev, cat, "todo o historico" if a.dias <= 0 else f"ultimos {a.dias:g} dias")
    por = collections.defaultdict(lambda: [0, 0, 0, 0, 0])  # chamadas, tokens in, tokens out, voltou (c), medidas
    for e in cham:
        p = por[e["exec"]]
        p[0] += 1
        p[1] += e.get("tokens_in") or 0
        p[2] += e.get("tokens_out") or 0
        c = e.get("devolvido_c", e.get("resposta_c"))
        if c is not None:
            p[3] += c
            p[4] += (e.get("tokens_in") or 0) + (e.get("tokens_out") or 0)
    print(f"por executor. 'voltou' = o que entrou no contexto de quem orquestra, estimado como caracteres/4.")
    print(f"{'executor':18} {'bolso':10} {'chamadas':>8} {'tokens in':>11} {'tokens out':>10} {'voltou':>8}")
    for eid, p in sorted(por.items(), key=lambda kv: -kv[1][1]):
        print(f"{eid:18} {cat.get(eid, {}).get('cota', '?'):10} {p[0]:>8} {p[1]:>11} {p[2]:>10} {p[3] // 4:>8}")
    medido, voltou = sum(p[4] for p in por.values()), sum(p[3] for p in por.values()) // 4
    if medido:
        print(f"nas chamadas medidas, os executores processaram {medido} tokens e ~{voltou} voltaram "
              f"({100 * voltou / medido:.1f}%). Chamadas antigas nao registravam o que voltou.")

    runs = [e for e in ev if e.get("evento") == "run"]
    finais = {e["job"]: e["evento"] for e in ev if e.get("evento") in ("juntar", "descartar")}
    ultimo = {e["job"]: e for e in runs}
    gasto = collections.Counter()
    for e in runs:
        gasto[e["job"]] += (e.get("tokens_in") or 0) + (e.get("tokens_out") or 0)
    achados = []
    peq = [j for j, e in ultimo.items() if finais.get(j) == "juntar" and 0 < (e.get("diff_linhas") or 0) <= 20]
    if peq:
        achados.append(f"{len(peq)} job(s) juntado(s) com diff de ate 20 linhas: era mais barato voce mesmo fazer")
    desc = [j for j, f in finais.items() if f == "descartar"]
    if desc:
        achados.append(f"{len(desc)} job(s) descartado(s), {sum(gasto[j] for j in desc)} tokens de executor sem resultado: "
                       "a especificacao estava fraca ou a parte nao era delegavel")
    longos = [j for j, e in ultimo.items() if e.get("rodada", 1) >= 3]
    if longos:
        achados.append(f"{len(longos)} job(s) com 3 ou mais rodadas: na 2a falha, assuma ou consulte acima")
    semv = [e for e in runs if e.get("rodada") == 1 and e.get("verificacao") is None and e.get("status") == "ok"]
    if semv:
        achados.append(f"{len(semv)} delegacao(oes) sem --verificar: conferir a mao custa mais tokens que um comando")
    fracas = [e for e in runs if e.get("rodada") == 1 and (e.get("avisos") or 0) >= 2]
    if fracas:
        achados.append(f"{len(fracas)} tarefa(s) enviada(s) com 2 ou mais avisos de especificacao")
    duv = [e for e in runs if e.get("status") == "DUVIDA"]
    if duv:
        achados.append(f"{len(duv)} vez(es) o executor parou com duvida: a decisao devia estar na tarefa")
    rep = collections.Counter(e["h"] for e in cham if e.get("h"))
    repetidos = sum(n - 1 for n in rep.values() if n > 1)
    if repetidos:
        achados.append(f"{repetidos} pedido(s) de leitura ou consulta repetido(s) com o mesmo texto")
    cons = [e for e in ev if e.get("evento") == "consulta"]
    if cons:
        print(f"consultas acima: {len(cons)}, {sum((e.get('tokens_in') or 0) + (e.get('tokens_out') or 0) for e in cons)} tokens")
    print("onde se perdeu:" if achados else "onde se perdeu: nada detectado no periodo")
    for x in achados:
        print("  -", x)
    if getattr(a, "comparar", False) and a.dias > 0:
        print()
        print(f"COMPARACAO (ultimos {a.dias:g} dias x {a.dias:g} dias anteriores)")
        print("indicador              anterior      atual")
        ind_ant = indicadores(ev_anterior)
        ind_atu = indicadores(ev_atual)
        linhas = [
            ("jobs", "jobs", lambda v: str(v), None),
            ("1a rodada ok", "primeira_ok", lambda v: f"{v}%", True),
            ("rodadas por job", "rodadas_media", lambda v: f"{v:.1f}", False),
            ("juntado", "juntado", lambda v: f"{v}%", True),
            ("tokens do orquestrador/job", "custo_por_juntado", lambda v: str(v), False),
        ]
        for label, chave, fmt, mais_melhor in linhas:
            v_ant = ind_ant.get(chave)
            v_atu = ind_atu.get(chave)
            s_ant = "-" if v_ant is None else fmt(v_ant)
            s_atu = "-" if v_atu is None else fmt(v_atu)
            seta = ""
            if mais_melhor is not None and v_ant is not None and v_atu is not None and v_ant != v_atu:
                if mais_melhor:
                    seta = "melhor" if v_atu > v_ant else "pior"
                else:
                    seta = "melhor" if v_atu < v_ant else "pior"
            tag = f"   {seta}" if seta else ""
            print(f"{label:26}{s_ant:>5}{s_atu:>11}{tag}")


def main():
    p = argparse.ArgumentParser(description="maestro: delegacao entre modelos")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("catalogo")
    c.add_argument("--completo", action="store_true")
    r = sub.add_parser("run")
    r.add_argument("texto", nargs="?")
    r.add_argument("--exec", default="auto", help="auto (rota do --tipo, na ordem OmniRoute > agy > Claude) ou lista a,b,c")
    r.add_argument("--sensivel", action="store_true", help="pula executores externos (OmniRoute)")
    r.add_argument("--tarefa")
    r.add_argument("--dir", default=".")
    r.add_argument("--tipo", help="ler | implementar | testes | refatorar | docs | pesquisar ...")
    r.add_argument("--verificar", help="comando rodado no worktree depois do executor; exit 0 = OK")
    r.add_argument("--continuar", metavar="JOB")
    r.add_argument("--timeout", type=int, default=1800)
    r.add_argument("--horas-cota", type=float, default=5)
    r.add_argument("--leitura", action="store_true", help="so coleta: roda na pasta real, sem worktree, sem editar")
    r.add_argument("--max-texto", type=int, default=6000)
    r.add_argument("--eu", help="seu id no catalogo ou seu nivel (1 a 4): delega so para nivel igual ou menor")
    r.add_argument("--escalar", type=int, default=1, help="quantas vezes o executor pode subir uma duvida sozinho")
    r.add_argument("--tentativas", type=int, default=2, help="correcoes automaticas quando o --verificar falha (0 desliga)")
    r.add_argument("--pode-nao-mudar", action="store_true",
                   help="verificacao OK sem nenhuma alteracao conta como OK (tarefa de so confirmar/diagnosticar)")
    r.add_argument("--orcamento", type=int, default=0, help="tokens do executor no job; atingido, a correcao automatica para (0 = sem limite)")
    r.add_argument("--pular-local", action="store_true", help="modelo local sem memoria livre: pula para o proximo da rota sem perguntar")
    r.add_argument("--sem-janela", action="store_true", help="modelo local sem memoria livre: em vez da janela, para com saida 5 (o orquestrador pergunta)")
    rc = sub.add_parser("recursos", help="RAM/VRAM livres comportam os modelos ollama? lista apps que liberariam memoria")
    rc.add_argument("--exec", help="um executor ollama do catalogo (padrao: todos)")
    lb = sub.add_parser("liberar", help="fecha apps para abrir memoria; SO com autorizacao do usuario")
    lb.add_argument("--apps", required=True, help="nomes da lista do 'recursos', separados por virgula")
    lb.add_argument("--forcar", action="store_true", help="mata o que nao fechou sozinho (perde o que nao foi salvo)")
    sub.add_parser("reabrir", help="reabre os apps que o maestro fechou e ainda nao reabriu")
    k = sub.add_parser("consultar", help="pergunta ao modelo mais barato acima de voce; so leitura")
    k.add_argument("texto", nargs="?")
    k.add_argument("--eu")
    k.add_argument("--tarefa")
    k.add_argument("--dir", default=".")
    k.add_argument("--min", type=int, help="nivel minimo de quem responde (ex.: 4)")
    k.add_argument("--sensivel", action="store_true")
    k.add_argument("--timeout", type=int, default=900)
    k.add_argument("--horas-cota", type=float, default=5)
    k.add_argument("--max-texto", type=int, default=4000)
    v = sub.add_parser("validar", help="Opus ou Fable (esforco medio) revisa o trabalho e da o veredito; so leitura")
    v.add_argument("texto", nargs="?", help="o que o usuario pediu, com as palavras dele")
    v.add_argument("--tarefa", help="arquivo .md com o pedido (em vez do texto)")
    v.add_argument("--modelo", choices=sorted(VALIDADORES), default="opus")
    v.add_argument("--dir", default=".")
    v.add_argument("--base", default="HEAD", help="revisa as mudancas desde esta ref (padrao: o que nao foi commitado)")
    v.add_argument("--job", help="revisa o worktree de um job do maestro em vez do projeto")
    v.add_argument("--verificar", help="comando rodado antes; o resultado vai junto para o revisor")
    v.add_argument("--anterior", choices=["auto", "nenhuma"], default="auto",
                   help="auto: manda junto a ultima validacao deste projeto nas ultimas 12 h (revalidacao)")
    v.add_argument("--esforco", choices=["medium", "high", "xhigh"], default="medium",
                   help="esforco do revisor; high para diff grande ou mudanca critica (gasta mais)")
    v.add_argument("--timeout", type=int, default=1500)
    v.add_argument("--horas-cota", type=float, default=5)
    v.add_argument("--max-texto", type=int, default=12000, help="resposta maior que isso e cortada no meio")
    li = sub.add_parser("licoes", help="licoes que as validacoes guardaram para este projeto (entram nas tarefas novas)")
    li.add_argument("--dir", default=".")
    o = sub.add_parser("rota", help="quem fica abaixo/igual e quem fica acima de voce; nao gasta nada")
    o.add_argument("--eu")
    o.add_argument("--tipo")
    o.add_argument("--sensivel", action="store_true")
    for nome in ("diff", "juntar", "descartar"):
        x = sub.add_parser(nome)
        x.add_argument("job")
        if nome == "diff":
            x.add_argument("--stat", action="store_true")
            x.add_argument("--arquivo", help="so este caminho")
            x.add_argument("--max", type=int, default=400, help="linhas; 0 = sem limite")
    sub.add_parser("jobs")
    q = sub.add_parser("cota")
    q.add_argument("id")
    q.add_argument("--horas", type=float, default=120)
    q.add_argument("--liberar", action="store_true")
    s = sub.add_parser("sugerir")
    s.add_argument("tipo", nargs="?")
    g = sub.add_parser("ganho", help="tokens processados pelos executores, o que voltou e onde se perdeu")
    g.add_argument("--dias", type=float, default=30, help="0 = todo o historico")
    g.add_argument("--comparar", action="store_true", help="compara o periodo com o periodo anterior de mesmo tamanho")
    a = p.parse_args()
    if a.cmd in ("run", "consultar", "validar", "liberar") and profundidade() >= 1:
        sys.exit("voce foi lancado pelo maestro (MAESTRO_EU definido): executor nao delega nem consulta. "
                 "Faca a tarefa; se faltar uma decisao, responda ESCALAR: <pergunta>")
    {"catalogo": cmd_catalogo, "run": cmd_run, "consultar": cmd_consultar, "validar": cmd_validar, "licoes": cmd_licoes, "rota": cmd_rota, "diff": cmd_diff, "juntar": cmd_juntar, "descartar": cmd_descartar,
     "jobs": cmd_jobs, "cota": cmd_cota, "sugerir": cmd_sugerir, "ganho": cmd_ganho,
     "recursos": cmd_recursos, "liberar": cmd_liberar, "reabrir": cmd_reabrir}[a.cmd](a)


if __name__ == "__main__":
    main()
