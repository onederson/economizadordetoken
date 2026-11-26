# Economizador de token (maestro)

> Cada parte da tarefa vai para o **menor modelo que da conta**. O modelo caro pensa, os baratos digitam.

O **maestro** e um orquestrador de modelos de linguagem para quem trabalha com agentes de codigo no terminal (Claude Code, Antigravity CLI/agy, opencode, Gemini CLI, Codex...). Ele divide o trabalho entre modelos de varias empresas e varios precos para que os modelos caros (Opus, Fable, Sonnet, Gemini Carbon) gastem token so com o que exige inteligencia: entender, decidir, especificar e conferir. Ler muito, tentar, errar e repetir vai para modelos gratuitos ou baratos.

Sao dois pecas:

| Peca | O que e |
|---|---|
| `maestro.py` | Ferramenta de linha de comando (Python puro, sem dependencias). Lanca o executor certo num **worktree git isolado**, roda a verificacao, devolve um resumo curto e registra tudo para medir o ganho. |
| `SKILL.md` | A skill `maestro`: as diretrizes que o modelo que esta orquestrando segue (quando delegar, quando consultar acima, como especificar, como conferir, o que nao fazer). Instalada em todos os CLIs. |

---

## Sumario

1. [O problema](#o-problema)
2. [A ideia em uma imagem](#a-ideia-em-uma-imagem)
3. [Niveis: descer o trabalho, subir a duvida](#niveis-descer-o-trabalho-subir-a-duvida)
4. [Requisitos](#requisitos)
5. [Instalacao](#instalacao)
6. [Primeiros passos](#primeiros-passos)
7. [Ciclo de vida de um job](#ciclo-de-vida-de-um-job)
8. [Escalada: quando o executor trava](#escalada-quando-o-executor-trava)
9. [Modelos locais (Ollama) e prioridade de memoria](#modelos-locais-ollama-e-prioridade-de-memoria)
10. [Validar: revisao de senior com Opus (medio ou maximo)](#validar-revisao-de-senior-com-opus-medio-ou-maximo)
11. [Sala de guerra (/warroom): varias frentes em paralelo](#sala-de-guerra-warroom-varias-frentes-em-paralelo)
12. [Referencia de comandos](#referencia-de-comandos)
13. [O catalogo](#o-catalogo)
14. [Como o maestro economiza token (sem ficar burro)](#como-o-maestro-economiza-token-sem-ficar-burro)
15. [Guarda: garante que o maestro e considerado](#guarda-garante-que-o-maestro-e-considerado)
16. [Seguranca](#seguranca)
17. [Testes](#testes)
18. [Usando de qualquer CLI](#usando-de-qualquer-cli)
19. [Dados e arquivos](#dados-e-arquivos)
20. [Limitacoes conhecidas](#limitacoes-conhecidas)
21. [Inspiracoes](#inspiracoes)

---

## O problema

Num agente de codigo, o modelo da sessao faz tudo: le arquivos inteiros, roda testes, le logs de mil linhas, tenta, erra, tenta de novo. Se a sessao e um modelo de topo, cada uma dessas leituras sai da cota mais cara que voce tem. Na pratica, a maior parte dos tokens vai para trabalho bracal que um modelo gratuito faria igual.

Ao mesmo tempo, delegar tudo para um modelo fraco da errado: ele chuta decisoes de arquitetura, diz "aprovado" com teste falhando e gasta tokens procurando o que nao foi especificado.

O maestro resolve as duas pontas:

- **trabalho bracal desce** para o modelo mais barato com cota disponivel, num ambiente isolado, com verificacao automatica;
- **decisoes sobem** como uma pergunta curta e fechada para o modelo mais barato que e mais forte que quem perguntou;
- **o que volta** para o orquestrador e so o resumo, nunca o log inteiro.

---

## A ideia em uma imagem

```
                         voce (usuario)
                               |
                               v
         +--------------------------------------------+
         |  ORQUESTRADOR (qualquer modelo, --eu N)    |
         |  entende, divide, especifica, confere      |
         +--------------------------------------------+
              |                                ^
   descer:    | maestro run                    | so o resumo:
   trabalho   | (nivel <= N)                   | alteracoes, verificacao,
   bracal     v                                | relatorio de 8 linhas
         +-----------------------+             |
         |  EXECUTOR barato      |-------------+
         |  worktree isolado     |
         |  Ollama > OmniRoute > |   travou numa decisao?
         |  agy > Claude Haiku   |-----> "ESCALAR: pergunta"
         +-----------------------+              |
                                                v
                                 +-------------------------------+
   subir:  maestro consultar --->|  CONSULTOR (nivel > executor) |
   so a duvida                   |  so leitura, ate 25 linhas    |
                                 |  DECISAO / MOTIVO / PASSOS    |
                                 +-------------------------------+
```

---

## Niveis: descer o trabalho, subir a duvida

Todo modelo do catalogo tem um **nivel** de 1 a 4:

| Nivel | Aguenta | Exemplos |
|---|---|---|
| **4** | arquitetura, causa raiz, seguranca, concorrencia, decisao sem volta | Fable, Opus, Gemini 4 Carbon, Gemini 4 Neon xhigh, GPT de topo |
| **3** | implementacao delicada, depuracao com muito contexto, revisar diff | Sonnet, Gemini 4 Neon medium/high, Gemini 4 Hydrogen high/xhigh, Gemini Pro, Kimi/GLM/DeepSeek grandes |
| **2** | implementar com especificacao fechada, testes, docs | Haiku, Gemini Flash, Gemini 4 Hydrogen medium/low, `ollama-qwen-code`, `ollama-gpt-oss`, free-code, modelos 'mini' |
| **1** | ler, coletar, extrair fatos, edicao mecanica | Flash low/lite, `ollama-gemma4`, OmniRoute free-fast |

O modelo que esta orquestrando informa quem e com `--eu` (o nivel, ou o seu id no catalogo). Para cada parte da tarefa ele pergunta: **qual e o menor nivel que resolve isto?**

| A parte exige | O que fazer |
|---|---|
| mais que o seu nivel | **subir**: `maestro consultar` com uma pergunta fechada; voce continua com a resposta |
| o seu nivel ou menos, e e pequena (< ~20 linhas num arquivo ja lido) | fazer voce mesmo: especificar custaria mais que fazer |
| o seu nivel ou menos, e e grande | **descer**: `maestro run --eu N` (coleta com `--leitura`) |

Assim o esquema funciona com **qualquer** modelo no comando: um Opus desce quase tudo e nao tem para onde subir; um Haiku desce leitura para um modelo nivel 1 e sobe decisoes de arquitetura para um nivel 3 ou 4.

**Ordem de gasto.** Dentro do mesmo nivel, o maestro respeita a ordem dos "bolsos" de cota: **1) Ollama local (gratis) 2) OmniRoute (gratis) 3) Google/agy 4) Anthropic/Claude**. So passa ao proximo bolso quando o anterior esta sem cota (ou quando o modelo local nao cabe na memoria e o usuario opta por pular).

**Qualidade antes de custo (aprendizado).** A ordem de gasto vale entre executores que entregam igual. Cada executor em cada job conta como uma tentativa, e ganha so quem entregou o trabalho que foi juntado. O local que falha e e salvo pelo agy leva a derrota. Vai para o fim da rota quem fica mais de 15 pontos abaixo do melhor da rota (com pelo menos 3 tentativas) ou perde 3 seguidas em qualquer tipo. Executor gratis que estoura o tempo, da erro ou termina sem alterar nada cede a vez na hora, e o worktree volta ao estado anterior. Servico fora do ar (ECONNREFUSED, 502/503) e pulado por 30 min sem contar derrota. Se o modelo ou a configuracao de um executor muda no catalogo, o historico antigo dele deixa de contar. `maestro sugerir` mostra o placar e a ordem efetiva de cada rota.

---

## Requisitos

- **Python 3.11+** (usa `tomllib`; nenhuma dependencia externa).
- **git** (o executor trabalha num `git worktree`; o projeto precisa ser um repositorio com pelo menos um commit).
- Pelo menos um CLI de executor no `PATH`:

| CLI | Usado para | Observacao |
|---|---|---|
| `ollama` + `claude` | modelos locais (`ollama-gemma4`, `ollama-qwen-code`, `ollama-gpt-oss`) | o Ollama serve em `http://127.0.0.1:11434` (`OLLAMA_API_URL`) e o maestro chama via `claude --bare` com `ANTHROPIC_BASE_URL` |
| `opencode` | modelos do **OmniRoute** (gratis e por credito) | precisa do provedor `omniroute` e dos agentes `worker` e `leitor` configurados (exemplo abaixo) |
| `agy` | Gemini Hydrogen/Neon/Carbon e Claude dentro do Antigravity (testado no agy 1.5.18) | usa a cota Google |
| `claude` | Haiku, Sonnet, Opus (e revisores `validar`) | usa a sua assinatura do Claude Code |

### Agentes do opencode

O maestro chama `opencode run --agent worker` (implementar) e `--agent leitor` (so leitura). Exemplo minimo em `~/.config/opencode/opencode.jsonc`:

```jsonc
{
  "agent": {
    "worker": {
      "description": "Executor barato: implementa tarefas delegadas e relata de forma curta.",
      "mode": "primary",
      "prompt": "Faca somente o que foi pedido. Rode os testes indicados. Nao faca commit nem push. Nunca afirme que testou algo que nao rodou.",
      "permission": {
        "bash": { "*": "allow", "git push*": "deny", "git commit*": "deny", "git reset*": "deny", "rm -rf*": "deny", "*.env*": "deny" }
      }
    },
    "leitor": {
      "description": "Leitor barato: coleta fatos sem alterar nada.",
      "mode": "primary",
      "prompt": "Voce coleta informacao para outro modelo. NAO altere arquivos. Devolva fatos literais (caminho:linha, mensagens, numeros).",
      "permission": { "edit": "deny", "bash": { "*": "allow", "git push*": "deny", "git commit*": "deny", "rm -rf*": "deny" } }
    }
  }
}
```

---

## Instalacao

```bash
git clone https://github.com/onederson/economizadordetoken.git
cd economizadordetoken
python instalar.py
```

**Para atualizar, e o mesmo comando:** `python instalar.py` dentro da pasta clonada. Ele baixa a versao nova (`git pull`) e instala tudo de novo.

O instalador:

- se a pasta e um clone do git, roda `git pull` antes e instala a versao nova (`--sem-atualizar` pula esse passo);
- copia `maestro.py`, `guarda.py` e `catalogo.toml` para `~/.maestro/`; se o `catalogo.toml` instalado era diferente, a versao antiga fica salva ao lado como `catalogo.toml.antes-AAAAMMDD-HHMMSS`;
- registra os hooks do `guarda.py` em `~/.claude/settings.json` de forma idempotente (use `python instalar.py --sem-guarda` para remover);
- copia as skills `maestro` e `warroom` e gera as skills `/claudevalidateopus` e `/claudevalidateopusmax` (a partir de `validar-skill.md`, removendo a skill de validacao antiga se existir) para cada CLI encontrado:

| Pasta | Quem le |
|---|---|
| `~/.claude/skills/maestro/` | Claude Code e opencode |
| `~/.gemini/antigravity-cli/skills/maestro/` | agy (Antigravity CLI) |
| `~/.agents/skills/maestro/` | Gemini CLI, Codex e outros que seguem o padrao `.agents` |

Pastas de CLIs que nao existem na maquina sao puladas.

Para conferir:

```bash
python ~/.maestro/maestro.py catalogo
python ~/.maestro/maestro.py rota --eu 2
```

---

## Primeiros passos

Abaixo, `maestro` = `python ~/.maestro/maestro.py`.

**1. Veja onde voce esta (nao gasta nada):**

```
$ maestro rota --eu cc-sonnet
voce: nivel 3
  descer/igual  run --tipo implementar  agy-hydrogen-high[3] > cc-haiku[2] > omni-free-code[2]
  descer/igual  run --tipo ler          ollama-gemma4[1] > ollama-gpt-oss[2] > omni-free-fast[1] > agy-hydrogen-low[2] > cc-haiku[2]
  ...
  subir         consultar              agy-neon-xhigh[4] > agy-carbon-high[4] > agy-opus[4] > cc-opus[4]
```

Executores com cota esgotada aparecem com `(X)`.

**2. Colete informacao com um modelo gratis:**

```
$ maestro run --leitura --eu 4 --dir ~/meu-projeto "onde a funcao get_meta e chamada? caminho:linha"
leitura | omni-free-fast (omniroute) | ok | 23s | tokens 61105/2368
src/api.py:42 ...
```

**3. Delegue uma implementacao com verificacao:**

```
$ maestro run --eu 4 --dir ~/meu-projeto --tipo implementar --tarefa tarefa.md --verificar "pytest -q"
job 20261112-114717-e59e | omni-free-code (omniroute, barato) | rodada 1 | ok | 22s
sessao: ses_e2fc... | tokens do executor: 39613/421
alteracoes:
 calc.py      | 3 +++
 test_calc.py | 6 +++++-
verificacao: OK (exit 0)
--- relatorio do executor (nao confie sem conferir) ---
RESULTADO: ...
proximo: maestro.py diff JOB | juntar JOB | run --continuar JOB "correcao" | descartar JOB
```

**4. Confira e aplique:**

```
maestro diff 20261112-114717-e59e --stat
maestro diff 20261112-114717-e59e
maestro juntar 20261112-114717-e59e     # aplica no projeto real, sem commit
```

**5. Tem uma decisao acima do seu nivel? Suba so ela:**

```
maestro consultar --eu 2 --dir ~/meu-projeto --tarefa duvida.md
```

### Modelo de tarefa

O que mais faz o executor barato acertar de primeira e uma especificacao fechada:

```markdown
# Tarefa: <nome curto>
## Contexto
- Arquivos e funcoes exatos ("em `get_meta` de `meta.py`"); o que ja existe e deve ser reaproveitado.
- Pegadinhas do ambiente.
- PROIBIDO: mexer em <X, Y>; imprimir segredos; instalar pacotes; comandos > 300 s.
## O que fazer
1. Comportamento exato, com exemplo de entrada e saida e numeros concretos.
## Fora de escopo
Tudo o resto.
## Verificacao
Comando(s) e resultado esperado.
```

---

## Ciclo de vida de um job

```
 run ──> worktree criado em ~/.maestro/wt/<job>  (copia do projeto, inclusive mudancas nao commitadas
  |                                              e arquivos nao rastreados)
  v
 executor trabalha com autonomia total no worktree (o projeto real nao e tocado)
  |
  v
 maestro roda --verificar no worktree ──> OK / FALHOU pelo exit code
  |        |
  |        +── FALHOU ──> correcao automatica: a falha volta ao executor e ele tenta de novo
  |                       (ate --tentativas, mesma falha 2x, 3 rodadas no job ou --orcamento)
  v
 resumo curto ──> voce confere: diff --stat, diff
  |
  +── aprovado ──> juntar     (git apply no projeto real; sem commit; worktree apagado)
  +── quase    ──> run --continuar JOB "o que falhou"   (mesma sessao e worktree, rodada +1)
  +── errado   ──> descartar  (worktree apagado)
```

- **`--verificar` substitui o "aprovado" do executor.** Modelos baratos as vezes dizem que testaram sem testar; o exit code nao mente.
- **Loop de verificacao sem custo para quem orquestra.** Se o `--verificar` falha, o proprio maestro devolve a saida enxuta ao executor, com a instrucao de nao apagar nem enfraquecer testes, e roda de novo. Para quando passa, quando a falha se repete igual (sem progresso; tempos, enderecos e numeros de linha nao contam como diferenca), quando o job chega a 3 rodadas, quando acabam as `--tentativas` (padrao 2) ou quando o executor estoura o `--orcamento` de tokens. Quem orquestra le so o resumo final: `correcao automatica: r1 omni-free-code FALHOU -> r2 agy-hydrogen-high OK`. Cada rodada entra no historico (o `sugerir` continua contando o acerto na 1a rodada) e as automaticas com custo zero para o orquestrador.
- **`--continuar` reaproveita a sessao** do executor (`--resume` no Claude, `--conversation` no agy, `--session` no opencode): ele nao rele o projeto do zero. Testado nos tres CLIs.
- **Gratis que falhou cede a correcao.** Com `--exec auto`, se a rodada anterior de um executor gratis (local/omniroute) deu `ERRO`, `TIMEOUT` ou verificacao reprovada, o `--continuar` manda a correcao para o proximo da rota, junto com a tarefa original (ele nao tem a sessao de quem comecou) e o trabalho parcial no worktree. Executor pago que falhou segue na mesma sessao.
- **Fallback de cota:** se o executor responde com erro de cota (429, `quota`, `rate limit`, `insufficient credit`...), o maestro marca ele como esgotado por `--horas-cota` horas (padrao 5) e tenta o proximo da lista, sozinho.
- **Jobs paralelos** sao seguros (cada um tem seu worktree); so evite dois jobs mexendo no mesmo arquivo, porque a juntada do segundo pode conflitar (o maestro tenta `git apply --3way` e, se o arquivo tambem mudou no projeto real enquanto o job rodava, `git apply -C1`, com 1 linha de contexto, antes de desistir; nesse caso avisa para conferir o diff).
- **Maximo recomendado: 3 rodadas.** Depois disso, quem orquestra corrige direto no worktree e junta, consulta acima ou descarta.

---

## Escalada: quando o executor trava

O executor recebe a instrucao de **nao chutar** decisoes que nao consegue tomar com seguranca (tarefa ambigua, duas abordagens com consequencias diferentes, causa de erro incerta depois de 2 tentativas). Nesses casos ele para e responde:

```
ESCALAR:
Como divide(a, b) deve se comportar ao dividir por zero?
Verifiquei que calc.py so tem funcoes simples e test_calc.py usa assert.
Opcoes: 1. devolver None  2. lancar ZeroDivisionError
```

O que acontece depende de quem chamou:

| Quem chamou (`--eu`) | Comportamento |
|---|---|
| **maior** que o executor | a pergunta volta para quem chamou: status `DUVIDA`, saida **4**. Responda com `maestro run --continuar JOB "decisao"`. Nao gasta um terceiro modelo, porque quem chamou ja e mais forte. |
| **igual ou menor** que o executor | o maestro chama `consultar` sozinho (modelo mais barato acima do executor), anexa a resposta a tarefa e o **mesmo executor continua na mesma sessao**. Aparece `escalou: omni-free-code -> omni-credit-strong` no resumo. |
| sem `--eu` | comportamento antigo: nunca consulta sozinho; a duvida volta (saida 4). |

`--escalar N` limita quantas consultas automaticas por rodada (padrao 1; `--escalar 0` desliga).

---

## Modelos locais (Ollama) e prioridade de memoria

Os modelos locais sao o **1o bolso** na ordem de gasto (`local > omniroute > google > anthropic`), rodando 100% na maquina e sem custo de API:

| Id | Modelo no Ollama | Nivel | Uso tipico | Memoria estimada |
|---|---|---|---|---|
| `ollama-gemma4` | `gemma4:12b-it-qat` | 1 | leitura, coleta, docs, tarefas pequenas | ~7 GB |
| `ollama-gpt-oss` | `gpt-oss:20b` | 2 | codigo geral, docs, fallback local nivel 2 | ~14 GB |
| `ollama-qwen-code` | `qwen3.6:35b-a3b-coding` | 2 | implementacao fechada, testes, refatoracao (MoE 35B-A3B) | ~22 GB |

- **Execucao via `claude --bare`:** o maestro aponta `ANTHROPIC_BASE_URL` para `http://127.0.0.1:11434` (ou `OLLAMA_API_URL`), define `timeout` proprio de `900` s e teto de saida `CLAUDE_CODE_MAX_OUTPUT_TOKENS` (`max_saida = 16000`). Se o modelo local travar ou entrar em loop, cede a vez ao proximo executor e registra `falha_local` no historico.
- **Validacao automatica de RAM + VRAM:** antes de rodar um executor `ollama` no `run`, o maestro consulta a memoria livre (RAM + VRAM disponivel, considerando se o modelo ja esta carregado no Ollama) e compara com o tamanho necessario:
  - **Cabe:** segue direto para o modelo local.
  - **Nao cabe, mas cabe fechando apps:** o modelo local tem prioridade. Uma janela avisa quanto falta, quais apps abertos seriam fechados (processos agrupados pelo app que os abriu; sistema, terminal, sessoes de IA e o proprio `ollama` sao protegidos) e qual seria o proximo executor caso nao feche.
    - **"Fechar" (ou sem resposta em 2 minutos):** fecha apenas os apps necessarios (forcando se nao fecharem sozinhos), salva a lista em `~/.maestro/reabrir.json`, executa o job local e, ao terminar, descarrega o modelo do Ollama (`keep_alive: 0`) e reabre os apps (navegadores voltam com `--restore-last-session` para restaurar as abas). Se mesmo depois de fechar ainda faltar memoria, reabre os apps na hora e passa ao proximo executor.
    - **"Nao fechar":** pula os modelos locais que nao cabem e segue a rota normalmente.
  - **Sem janela possivel (ou com `--sem-janela`):** o `run` para com codigo de saida **5** (`PC SEM FOLGA PARA O MODELO LOCAL`) e lista os apps liberaveis. O orquestrador pergunta ao usuario, roda `maestro liberar --apps a,b` (somente apos autorizacao) e repete o `run`, ou repete com `--pular-local`.
  - **Recuperacao:** se o maestro for interrompido antes de reabrir os apps, rode `maestro reabrir`.

---

## Validar: revisao de senior com Opus (medio ou maximo)

Trabalhou com um modelo menor (agy com Hydrogen ou Neon, Claude com Haiku, um modelo local...) e quer saber se esta mesmo certo? Digite **`/claudevalidateopus`** ou **`/claudevalidateopusmax`** na sessao, com um foco opcional depois do comando (`/claudevalidateopus confira a migracao do banco`).

A skill faz o modelo da sessao, seja qual for, juntar o que voce pediu (com as suas palavras) e rodar:

```
maestro validar --modelo opus|opusmax --dir PROJ --tarefa pedido.md [--base REF | --job JOB] [--verificar "cmd"]
```

O maestro grava o diff completo em `~/.maestro/validacoes/<id>/` e chama o **Claude Opus 5.5** com **esforco medio** (`opus`) ou **esforco maximo** (`opusmax`), so leitura, no papel de um engenheiro senior com 50 anos de experiencia. Ele confere, nesta ordem: os itens da validacao anterior (se houver), se o pedido foi atendido por inteiro (e o que foi feito sem pedido), se todo estado que o trabalho abre fecha em todos os caminhos (erro, timeout, cancelamento, troca de modo), quem chama o que mudou, se os testes cobrem os caminhos de falha (ele roda os testes), e seguranca, segredos e sobras.

**Regua fixa**, para o veredito nao depender do humor do revisor nem subir a cada rodada:

- `[bloqueia]`: no uso pedido, resultado errado, dado perdido ou exposto, travamento, ou algo nao pedido com efeito externo. Da REPROVADO.
- `[importante]`: bug real num cenario plausivel, ou teste que deixaria esse bug passar. Da APROVADO COM RESSALVAS.
- Os dois exigem **cenario concreto** (entrada -> resultado errado), de preferencia reproduzido. Sem cenario, nao e problema.
- Cada problema vem como **classe**: todas as ocorrencias, a invariante que a correcao deve garantir e o teste que pega a classe inteira. Isso evita a correcao que conserta 1 de 4 caminhos e gera outra rodada.
- **Origem**: `[spec]` (a especificacao nao disse e o executor nao tinha como saber) ou `[executor]` (estava dito, ou e engenharia basica).
- Problema antigo, o que so se confirma ao vivo e ja foi declarado pendente, estilo e melhorias nao pedidas vao em **FORA DO VEREDITO** e nao pesam. Sem `[bloqueia]` nem `[importante]`, o veredito e **APROVADO**.

```
VEREDITO: REPROVADO
RESUMO: o filtro por data foi feito, mas a paginacao quebrou.
PROBLEMAS:
1. [bloqueia] [spec] api/lista.py:88 e api/busca.py:41 calculam o offset antes do filtro; pagina 2 com filtro
   repete itens. Invariante: o offset sempre se aplica a lista ja filtrada. Teste: filtro + pagina 2 nos dois.
FORA DO VEREDITO: api/lista.py:12 nome confuso (antigo).
LICOES:
- Toda tarefa que mexe em listagem diz se filtro, ordenacao e paginacao se aplicam nessa ordem.
CONFERIDO: pytest -q -> 41 passed, 1 failed (test_paginacao); li api/lista.py e quem chama listar(); nao li web/.
```

**O maestro aplica a regua:** o veredito sai das etiquetas da secao PROBLEMAS, nao da impressao do revisor. Se ele escrever "RESSALVAS" listando so `[menor]`, sai `VEREDITO PELA REGUA: APROVADO`, e o codigo de saida segue a regua. O historico guarda os dois (`veredito` e `veredito_dito`).

**Revalidacao:** a validacao anterior do mesmo projeto (ate 12 h, pulando as que cairam sem veredito) vai junto sozinha. Cada validacao guarda uma foto do estado do projeto (uma arvore git feita num indice temporario, sem tocar o seu indice), e a seguinte recebe o **diff desde a anterior**: o revisor comeca pela correcao em vez de reler tudo. Ele diz, item a item, o que foi corrigido, nao reaponta o que o usuario decidiu nao corrigir e so traz problema novo se for `[bloqueia]` ou `[importante]`, dizendo se veio da correcao ou se escapou antes.

**Diff grande:** em esforco medio o revisor le por amostragem (as validacoes reais levaram de 44 a 136 s, mesmo com diffs de 5 a 12 mil linhas), entao cada rodada pode achar coisa nova. Acima de 4000 linhas o maestro avisa e sugere `--esforco high` ou validar por partes.

**Licoes:** as linhas de `LICOES` (problemas de origem `[spec]`) ficam em `~/.maestro/licoes/<projeto>.md`, sem repetir, ate 40. Cada `maestro run` novo nesse projeto anexa as ultimas 15 a tarefa, e `maestro licoes --dir PROJ` mostra todas. Assim o que reprovou por falta de especificacao entra sozinho na proxima especificacao. O `run` tambem avisa quando uma tarefa grande nao diz o que acontece nos caminhos de falha.

| Opcao | Padrao | O que faz |
|---|---|---|
| `texto` ou `--tarefa ARQ` | obrigatorio | o que o usuario pediu; sem isso o revisor nao tem contra o que julgar |
| `--modelo opus\|opusmax` | `opus` | `claude-opus-5-5`, com esforco medio (`opus`) ou maximo (`opusmax`) |
| `--base REF` | `HEAD` | revisa as mudancas desde essa ref, mais arquivos novos. Padrao: o que nao foi commitado. Trabalho ja commitado: `--base HEAD~2`, `--base main` |
| `--job JOB` | | revisa o worktree de um job do maestro antes de juntar (a especificacao entregue ao executor vai junto) |
| `--verificar "cmd"` | | roda antes e manda o resultado ao revisor |
| `--anterior auto\|nenhuma` | `auto` | `auto` manda junto a ultima validacao do projeto nas ultimas 12 h; `nenhuma` quando e outro trabalho |
| `--esforco medium\|high\|xhigh\|max` | vazio (do `--modelo`) | usa `medium` para `opus` e `max` para `opusmax`; se passado, sobrepoe (`high` para diff grande ou mudanca critica; gasta mais) |
| `--max-texto N` | `12000` | resposta maior que isso volta com o meio cortado (a completa fica gravada) |
| `--timeout S` | `1500` | |

Saida: **0** aprovado (com ou sem ressalvas), **3** reprovado, **1** erro ou resposta sem `VEREDITO`, **2** timeout. Toda validacao entra no historico (`ganho` mostra o gasto).

- Os dois revisores ficam **fora do catalogo** de proposito: o `consultar` e as rotas nunca gastam `opus` ou `opusmax` sozinhos.
- As skills tem `disable-model-invocation`: so voce dispara, porque cada validacao gasta um modelo de topo.
- Os dois gastam a mesma cota do Claude: se um esta sem cota, o outro tambem; a mensagem de erro diz para tentar mais tarde.

---

## Sala de guerra (/warroom): varias frentes em paralelo

Quando ha varias atividades na fila, o orquestrador abre uma **sala de guerra**: cada atividade vira uma **frente**, todas rodam ao mesmo tempo em executores diferentes (varios agy, Haiku, Ollama...) e nenhuma atrapalha a outra. Nasceu numa sessao real de desenvolvimento de jogo, em que 4 executores trabalharam juntos e 12 frentes entraram em uma manha; o `maestro sala` automatiza o que la foi feito a mao (lancar com `&`, vigiar logs, conferir quem mexeu onde, juntar e commitar uma por uma).

Como funciona:

- **Plano** (`sala.toml`): uma `[[frente]]` por atividade, com o arquivo de tarefa, a **area** (globs dos arquivos que ela pode criar/alterar) e, se for o caso, `depois = [...]`. Na sala: `verificar`, `paralelo`, `juntar = "revisar" | "auto"`, `protegidos` (arquivos existentes que nenhuma frente pode alterar, como os testes) e `rodape` para os commits.
- **Sem pisar no outro:** frentes cujas areas se cruzam, ou com `depois`, esperam a anterior ser **juntada** e partem do projeto ja com ela. O maestro anexa a cada tarefa as regras da area; ao terminar, confere o diff: arquivo fora da area ou protegido alterado = estado `invadiu`, nada e juntado.
- **Juntada uma por vez:** aplica no projeto real, roda o `verificar` **ali** (pega o conflito de sentido entre duas frentes que passaram cada uma sozinha), faz o commit da frente e apaga o worktree. Falhou qualquer passo: desfaz, o projeto fica como estava e a frente vai para `conflito`.
- **Vigia:** `sala rodar` lanca o que esta livre (processos soltos, que sobrevivem se o vigia fechar), olha a cada 10 s e termina no primeiro evento que precisa do orquestrador (`pronta`, `falhou`, `duvida`, `invadiu`, `conflito`), imprimindo o quadro e os proximos comandos. Rodado em background, o fim dele e o aviso.
- **Modelo local:** so uma frente por vez usa o Ollama (GPU); as outras seguem a rota sem ele. Se o local nao couber na memoria, a frente e relancada sem ele, sem janela.
- **Plano vivo:** acrescentar `[[frente]]` no `.toml` e rodar `sala rodar` de novo inclui a frente nova no meio da guerra.

```
maestro sala checar sala.toml        # confere o plano e mostra as ondas; nada e lancado
maestro sala rodar sala.toml         # em background; volta no proximo evento
maestro diff <job> --stat            # conferir a frente pronta
maestro sala juntar <sala> <id>      # aplica, verifica no projeto real, commita (desfaz se falhar)
maestro sala retomar <sala> <id> "correcao"   # mesmo job, nova rodada
maestro sala refazer <sala> <id>     # descarta o job e parte de novo do projeto atual
maestro sala status|parar|descartar ...
```

Exemplo de plano:

```toml
dir = "C:/Projetos/Jogo"
verificar = "bash tools/verificar.sh"
paralelo = 4
juntar = "revisar"
protegidos = ["testes/nucleo/**"]

[[frente]]
id = "B"
titulo = "feat: gerador da run"
tarefa = "tarefa_run.md"
area = ["scripts/run/**", "testes/run/**"]

[[frente]]
id = "G"
titulo = "feat: liga barulho e infeccao nos zumbis"
tarefa = "tarefa_integracao.md"
area = ["scripts/zumbis/**", "scripts/jogador/jogador.gd", "testes/integracao/**"]
depois = ["B"]
```

A skill `/warroom` (instalada junto) ensina o orquestrador a levantar a fila, dividir em frentes que nao se cruzam, escrever o plano e tratar cada evento. Dados de cada sala em `~/.maestro/salas/<nome>/` (`sala.json`, um `.log` por frente).

---

## Referencia de comandos

| Comando | Para que |
|---|---|
| `maestro catalogo [--completo]` | executores, nivel, bolso, custo, flags (`poupar`, `externo`, `ESGOTADO`) e rotas |
| `maestro rota --eu N [--tipo T] [--sensivel]` | quem esta abaixo/igual e acima de voce, ja na ordem que o `run` vai usar (avisa quem desceu por taxa de acerto). Nao gasta nada |
| `maestro run ...` | delegar (ver abaixo) |
| `maestro consultar --eu N "duvida"` | subir uma decisao para o modelo mais barato acima de voce |
| `maestro validar --modelo opus\|opusmax "pedido"` | revisao de senior do trabalho feito (ver [Validar](#validar-revisao-de-senior-com-opus-medio-ou-maximo)) |
| `maestro licoes [--dir PROJ]` | licoes que as validacoes guardaram para o projeto (entram sozinhas nas tarefas novas) |
| `maestro diff JOB [--stat] [--arquivo CAMINHO] [--max 400]` | o que o executor mudou |
| `maestro juntar JOB` | aplica no projeto real (sem commit) e apaga o worktree |
| `maestro descartar JOB` | apaga o worktree sem aplicar |
| `maestro jobs` | jobs com worktree aberto |
| `maestro sala checar\|rodar\|status\|juntar\|retomar\|refazer\|descartar\|parar ...` | sala de guerra: varias frentes em paralelo (ver [Sala de guerra](#sala-de-guerra-warroom-varias-frentes-em-paralelo)) |
| `maestro cota ID --horas 120` / `--liberar` | marcar ou liberar cota esgotada manualmente |
| `maestro sugerir [tipo]` | taxa de acerto por executor e tipo (verificacao OK, OK de primeira, juntado) |
| `maestro recursos [--exec ID]` | a RAM + VRAM livres comportam o modelo ollama? Quanto falta e quais apps liberariam memoria. O `run` faz essa conta sozinho antes de usar um executor local e, se nao cabe, o modelo local tem prioridade: uma janela avisa quais apps vao ser fechados. "Fechar" ou sem resposta em 2 min: fecha so o necessario (forcando o que nao fechar), roda e, no fim, tira o modelo da memoria e reabre os apps (navegador com as abas). "Nao fechar": segue a rota sem o local. Sem janela (ou `--sem-janela`), para com saida 5 |
| `maestro reabrir` | reabre os apps que o maestro fechou e ainda nao reabriu (ex.: o maestro caiu no meio). A lista fica em `~/.maestro/reabrir.json` |
| `maestro liberar --apps a,b [--forcar]` | fecha so os apps listados (pedido normal de fechar; `--forcar` mata o que ficou aberto). Rode **somente depois de o usuario autorizar**; sistema, terminal, sessoes de IA e o ollama sao protegidos. Sem autorizacao: `run ... --pular-local` |
| `maestro ganho [--dias 30] [--comparar]` | resumo da economia (tokens que nao passaram pelo orquestrador, ja descontado o custo de mandar a tarefa e ler a resposta), tokens por executor, quanto voltou, onde se perdeu. `--dias 0` = todo o historico; `--comparar` compara o periodo com o anterior de mesmo tamanho |

### `run`

| Opcao | Padrao | O que faz |
|---|---|---|
| `texto` ou `--tarefa ARQ` | | a tarefa (texto direto ou arquivo .md) |
| `--dir PASTA` | `.` | projeto (repositorio git com commit) |
| `--eu N\|ID` | | seu nivel; a rota automatica so usa executores de nivel <= N |
| `--tipo T` | `implementar` | escolhe a rota: `ler`, `implementar`, `pequena`, `testes`, `refatorar`, `docs`, `pesquisar`, `dificil` |
| `--exec auto\|a,b,c` | `auto` | `auto` usa a rota do tipo; ou uma lista propria, na ordem |
| `--verificar "cmd"` | | rodado no worktree depois do executor; exit 0 = OK |
| `--leitura` | | so coleta, sem worktree e sem editar: le a pasta real (o agy roda em `~/.maestro/wt/_leitura` com o projeto adicionado por `--add-dir`, ver [Executor sem memoria](#guarda-garante-que-o-maestro-e-considerado)) |
| `--continuar JOB` | | nova rodada no mesmo job (mesma sessao e worktree) |
| `--sensivel` | | pula executores externos (`privado = false`, o OmniRoute) |
| `--escalar N` | `1` | consultas automaticas acima por rodada |
| `--tentativas N` | `2` | correcoes automaticas quando o `--verificar` falha (`0` desliga) |
| `--pode-nao-mudar` | | verificacao OK sem nenhuma alteracao conta como OK (tarefa de so confirmar ou diagnosticar); sem ela, job que nao alterou nada e falha |
| `--orcamento T` | `0` | tokens do executor no job; atingido, a correcao automatica para (`0` = sem limite) |
| `--timeout S` | `1800` | limite do executor, em segundos |
| `--horas-cota H` | `5` | por quanto tempo marcar um executor como esgotado |
| `--max-texto C` | `6000` | limite da resposta de `--leitura` que volta ao orquestrador |
| `--pular-local` | | modelo local sem memoria livre: pula para o proximo da rota sem perguntar |
| `--sem-janela` | | modelo local sem memoria livre: em vez da janela, para com saida 5 para o orquestrador perguntar |

A rota `dificil` (`omni-credit-strong > agy-hydrogen-xhigh > agy-neon-high > agy-carbon-high > agy-opus > cc-sonnet`) e a unica que ignora o teto do `--eu`: e o caminho explicito para **executar** acima do seu nivel.

### `consultar`

| Opcao | Padrao | O que faz |
|---|---|---|
| `texto` ou `--tarefa ARQ` | | a duvida |
| `--eu N\|ID` | obrigatorio | seu nivel; responde o mais barato acima dele |
| `--min N` | | exige um nivel minimo de quem responde (ex.: `--min 4` para arquitetura) |
| `--dir PASTA` | `.` | projeto que o consultor pode ler |
| `--sensivel` | | pula externos |
| `--timeout S` | `900` | |

O consultor roda em modo so leitura e responde em ate 25 linhas: **DECISAO, MOTIVO, PASSOS** (com arquivo e funcao) e **RISCOS**. Ele entrega a decisao pronta, sem fazer o trabalho do modelo menor.

### Codigos de saida do `run`

| Codigo | Significado |
|---|---|
| 0 | ok |
| 1 | erro do executor |
| 2 | timeout |
| 3 | executor terminou, mas a verificacao falhou |
| 4 | executor parou com uma duvida (`ESCALAR`) que voltou para voce |
| 5 | PC sem memoria livre para o modelo local (com `--sem-janela` ou sem janela disponivel) |

---

## O catalogo

`~/.maestro/catalogo.toml` define executores, rotas e a ordem dos bolsos.

```toml
bolsos = ["local", "omniroute", "google", "anthropic"]   # ordem de gasto

[rotas]   # na rota automatica, quem tem taxa de acerto baixa no tipo desce para o fim (ver "Rota por taxa de acerto")
implementar = ["agy-hydrogen-high", "omni-free-code", "cc-haiku"]
pequena     = ["ollama-gpt-oss", "omni-free-fast", "agy-hydrogen-medium", "ollama-gemma4", "cc-haiku"]
testes      = ["agy-hydrogen-high", "omni-free-code", "cc-haiku"]
refatorar   = ["agy-hydrogen-high", "omni-free-code", "cc-haiku"]
docs        = ["ollama-gpt-oss", "ollama-gemma4", "omni-free-fast", "agy-hydrogen-low", "cc-haiku"]
ler         = ["ollama-gemma4", "ollama-gpt-oss", "omni-free-fast", "agy-hydrogen-low", "cc-haiku"]
pesquisar   = ["agy-hydrogen-medium", "omni-free-code", "cc-haiku"]
dificil     = ["omni-credit-strong", "agy-hydrogen-xhigh", "agy-neon-high", "agy-carbon-high", "agy-opus", "cc-sonnet"]

[agy-hydrogen-high]
cli = "agy"                          # claude | agy | opencode
modelo = "gemini-4-hydrogen-high"    # passado ao CLI
cota = "google"                      # de qual bolso gasta
custo = "medio"                      # barato | medio | caro (relativo, dentro do bolso)
nivel = 3                            # 1 a 4 (sem isso: barato=2, medio/caro=3)
privado = true                       # false = provedor externo pode registrar; pulado com --sensivel
forte = "implementacao com especificacao fechada, testes, refatoracao"
fraco = "precisa de arquivos e funcoes nomeados; confira o diff, nao o APROVADO que ele escreve"
# poupar = true                      # so com motivo; o orquestrador avisa o usuario quando usa
```

Executores que vem no catalogo:

| Id | CLI | Nivel | Bolso | Flags |
|---|---|---|---|---|
| `ollama-gemma4` | ollama | 1 | local | |
| `ollama-qwen-code` | ollama | 2 | local | |
| `ollama-gpt-oss` | ollama | 2 | local | |
| `omni-free-fast` | opencode | 1 | omniroute | externo |
| `omni-free-code` | opencode | 2 | omniroute | externo |
| `omni-free-safe` | opencode | 2 | omniroute | externo |
| `omni-credit-strong` | opencode | 3 | omniroute | externo, poupar |
| `agy-hydrogen-low` | agy | 2 | google | |
| `agy-hydrogen-medium` | agy | 2 | google | |
| `agy-hydrogen-high` | agy | 3 | google | |
| `agy-neon-medium` | agy | 3 | google | |
| `agy-neon-high` | agy | 3 | google | |
| `agy-hydrogen-xhigh` | agy | 3 | google | |
| `agy-sonnet` | agy | 3 | google | poupar |
| `agy-neon-xhigh` | agy | 4 | google | poupar |
| `agy-carbon-high` | agy | 4 | google | poupar |
| `agy-opus` | agy | 4 | google | poupar |
| `cc-haiku` | claude | 2 | anthropic | |
| `cc-sonnet` | claude | 3 | anthropic | poupar |
| `cc-opus` | claude | 4 | anthropic | poupar |

> **Mudanca no agy (2026-11-30):** o agy remove Gemini 3.8 Flash, Gemini 4 Argon e GPT-OSS-120b ([modelos do agy](https://antigravity.google/docs/models)). O Gemini 4 Hydrogen substitui o Flash por esforco (`agy-hydrogen-low`/`medium`/`high`), o `agy-hydrogen-xhigh` ocupa o lugar do Argon na rota `dificil`, e o agy tambem aceita `--effort` (`low|medium|high|xhigh|max`) mantendo os ids com sufixo validos.

Para adicionar um executor, crie um bloco novo com `cli` em `claude`, `ollama`, `agy` ou `opencode`. Outros CLIs exigem um adaptador novo em `montar()` e `interpretar()` no `maestro.py`.

---

## Como o maestro economiza token (sem ficar burro)

Economizar token cortando informacao e facil; o dificil e cortar **sem** que o orquestrador passe a decidir errado. As regras abaixo foram inspiradas no [headroom](https://github.com/headroomlabs-ai/headroom) e no [rtk](https://github.com/rtk-ai/rtk): **corte o ruido, mantenha o sinal, e sempre diga onde esta o original.**

### O que o maestro faz automaticamente

| Mecanismo | Como funciona | Salvaguarda |
|---|---|---|
| **Resumo em vez de log** | do executor volta so: alteracoes (`--stat`), resultado da verificacao e um relatorio de no maximo 8 linhas | o log completo fica em `~/.maestro/jobs/<job>/` |
| **Verificacao enxuta** | quando a verificacao falha, voltam so as linhas com sinal de erro (`error`, `fail`, `assert`, `Traceback`, `File "..."`...) com 1 linha de contexto, mais o inicio e o fim; linhas repetidas viram `(xN)` | saida curta (<= 40 linhas) volta **identica**; a saida inteira e gravada e o caminho vem no resumo; se o filtro falhar, volta o fim bruto |
| **Verificacao vazia reprova** | se a saida mostra que nenhum teste rodou (`Ran 0 tests`, `NO TESTS RAN`, `collected 0 items`...), a verificacao conta como FALHOU mesmo com exit 0 | o unittest do Python < 3.12 sai com 0 sem achar teste; um executor que nao entregou o teste passaria |
| **Modelo local nao prende o job** | executores `ollama` tem `timeout` proprio no catalogo (900 s) e teto de saida (`max_saida`, 16000 tokens); erro ou timeout no local passa para o proximo executor e grava `falha_local` no historico | antes, um modelo local em loop gerava ate 32 mil tokens e segurava o job por ~20 min |
| **Diff com teto** | `diff` para em 400 linhas e avisa | o resto se le por arquivo (`--arquivo`) ou inteiro (`--max 0`); diff cortado nao conta como conferido |
| **Resposta longa guardada** | leitura ou consulta acima do limite volta com inicio + fim + caminho do texto completo | nada se perde: o completo fica em `~/.maestro/respostas/` |
| **Avisos antes de gastar** | `run` avisa quando a tarefa sai sem arquivo citado, sem `--verificar` ou pequena demais para valer a delegacao; `consultar` avisa quando a pergunta e vaga | aviso nao bloqueia |
| **Correcao automatica** | verificacao reprovada volta ao executor sem passar pelo orquestrador (ver [Ciclo de vida de um job](#ciclo-de-vida-de-um-job)) | para em 3 rodadas, na mesma falha repetida ou no `--orcamento` |
| **Sessao reaproveitada** | `--continuar` retoma a sessao do executor em vez de abrir outra que releria tudo | |
| **Rota por taxa de acerto** | na rota automatica, o executor com menos da metade dos jobs juntados naquele tipo (minimo 5 encerrados, ultimos 30 dias) vai para o fim da fila; `maestro rota` mostra quem desceu e por que | token gratis que vira descarte custa token caro de revisao; o rebaixado continua de reserva e volta quando o historico envelhece |
| **Executor Claude enxuto** | o Haiku/Sonnet roda com `--strict-mcp-config --disable-slash-commands` (sem servidores MCP e sem skills) | numa leitura simples, a entrada caiu de ~100-157 mil para ~60 mil tokens (tarefas diferentes, comparacao aproximada) |
| **Executor agy enxuto** | o agy roda com `--disable-slash-commands` (sem expansao de skills e comandos) e o raciocinio (`thinking_tokens`) entra na conta de saida do executor no `ganho` | o modo padrao do `agy -p` virou Auto no 1.5.7; a autonomia continua explicita com `--dangerously-skip-permissions` |
| **Sem cascata** | o executor nao pode delegar de novo: `maestro run`/`consultar` recusam rodar dentro de um executor (`MAESTRO_EU` definido) | |
| **Medicao** | `maestro ganho` abre com a economia liquida (tokens feitos pelos executores em trabalho aproveitado, menos o que o orquestrador gastou mandando tarefas e lendo respostas), depois tokens por executor, quanto voltou ao orquestrador (estimado em caracteres/4) e onde se perdeu | a economia e um teto: o executor rele o contexto a cada turno, entao o orquestrador faria o mesmo trabalho com menos tokens |

Formato do `ganho` (numeros ilustrativos):

```
ECONOMIA (ultimos 30 dias)
  tokens que nao passaram pelo orquestrador: 1.743.104
    = 1.767.041 feitos pelos executores em trabalho aproveitado (98.545 em modelo gratis: local/omniroute)
    - 23.937 que o orquestrador gastou mandando tarefas e lendo respostas
  fora da conta: 41.210 tokens de executor em jobs descartados
  teto: o executor rele o contexto a cada turno; o orquestrador faria o mesmo trabalho com menos tokens que isso.

por executor. 'voltou' = o que entrou no contexto de quem orquestra, estimado como caracteres/4.
executor           bolso      chamadas   tokens in tokens out   voltou
agy-hydrogen-high  google            4     1416551     251945      612
omni-free-code     omniroute         5       91962       6583      880
consultas acima: 1, 24053 tokens
onde se perdeu:
  - 3 job(s) descartado(s), 41210 tokens de executor sem resultado: a especificacao estava fraca ou a parte nao era delegavel
  - 1 job(s) juntado(s) com diff de ate 20 linhas: era mais barato voce mesmo fazer
```

### Diretrizes da skill: requisicoes que so queimam token

- delegar o que cabe em ~20 linhas de um arquivo que voce ja leu;
- pedir de novo o que ja esta no seu contexto, ou reler arquivo ja lido;
- mandar "explore o projeto" ou "veja o que acha": toda leitura tem pergunta fechada e formato de resposta;
- subir uma duvida sem as opcoes que voce ve e sem o que ja verificou;
- abrir job novo para corrigir um job (use `--continuar`);
- pedir o log, o diff ou a saida inteira antes de olhar o resumo;
- subir o que um teste responde.

### Diretrizes da skill: onde NAO economizar

- o pedido do usuario entra na especificacao com as palavras dele, sem parafrasear requisito;
- mensagens de erro, caminhos, numeros e nomes vao literais, nunca resumidos;
- a conferencia do diff e a verificacao nao se pulam;
- ambiguidade real vira pergunta ao usuario, nao chute;
- resumo cortado sempre diz onde esta o completo: se o resumo nao explica a falha, abra o completo em vez de adivinhar.

### O que ficou de fora de proposito

- **Cache de respostas repetidas:** uma leitura de log mudaria sem o cache perceber, e resposta velha e exatamente o "ficar burro". O `ganho` so conta repeticoes.
- **Compressao por modelo de ML / proxy na frente da API:** pesado e redundante com o rtk.
- **Compactar o contexto em 40% (ou 60% em tarefa grande):** avaliado em 2026-11-19. Compactar gasta tokens para gerar o resumo, perde detalhe (caminhos, saidas literais) e invalida o cache do prompt, e contexto em cache sai por ~10% do preco. Um hook tambem nao sabe se a tarefa e "grande". O que fica: fechar a fase (job juntado e verificado), registrar o que falta e comecar a proxima com o contexto limpo. Delegar a leitura pesada ao maestro ja mantem o contexto de quem orquestra pequeno.
- **Skill que se reescreve sozinha:** avaliado em 2026-11-19. Sem uma metrica que julgue, o texto so cresce (fica mais caro a cada carga), piora sem ninguem ver e quebra o cache do prompt a cada sessao. O que fica: o aprendizado automatico e a rota por taxa de acerto (dado, nao texto), e mudanca na skill, no catalogo ou nas rotas so permanece se `maestro ganho --dias 7 --comparar` mostrar melhora.
- **Agente "orquestrador" e pasta `memoria/` dentro do projeto:** a sessao principal ja e o orquestrador; um agente a mais repetiria o contexto. Memoria ja ha (ai-memory e a memoria do Claude Code); uma terceira geraria contradicao.
- **Passar a saida da verificacao pelo `rtk pipe`:** testado em 2026-11-14 com o rtk 1.2.0: com o pytest ausente, a saida "No module named pytest" virou "Pytest: No tests collected", escondendo o erro real. O `enxugar` do maestro preserva as linhas de erro, entao fica ele.

---

## Seguranca

- **Worktree isolado.** O executor tem autonomia total, mas so dentro de `~/.maestro/wt/<job>`. Nada chega ao projeto real sem `juntar`, e `juntar` nao faz commit.
- **Regras do executor:** nao fazer commit, push, reset nem checkout; nao rodar nada em segundo plano; nao imprimir chaves; nao instalar pacotes que a tarefa nao pede; nao sair do escopo.
- **`--sensivel`** pula executores com `privado = false` (provedores gratuitos podem registrar o que recebem). Use para codigo de cliente ou com segredo.
- **Nunca coloque segredos** em tarefas ou consultas.
- **Junctions e symlinks no Windows:** antes de apagar um worktree, o maestro desfaz links sem seguir o destino. Em Python < 3.12, `shutil.rmtree` e `git worktree remove` entram em junctions e apagam a pasta real para onde elas apontam; isso ja esvaziou um projeto uma vez, por isso a protecao.
- **Lixo de build** (`__pycache__`, `*.pyc`, `node_modules`, `.pytest_cache`, `*.log`) nunca entra no patch.
- **Consulta e leitura** rodam em modo so leitura (`--disallowedTools Edit Write` no Claude, `--mode plan` no agy, agente `leitor` no opencode).

---

## Guarda: garante que o maestro e considerado

Uma skill so funciona se o modelo lembrar dela, e na pratica ele esquecia: sessoes com centenas de chamadas e nenhuma delegacao. O `guarda.py` sao hooks do Claude Code que rodam sempre, sem depender da memoria do modelo:

| Quando | O que o guarda faz |
|---|---|
| Tarefa nova (mensagem com mais de 40 caracteres) | coloca no contexto o criterio de quando delegar e exige a primeira linha `maestro: sim - ...` ou `maestro: nao - motivo` |
| A cada 12 chamadas de execucao (ler, buscar, editar, rodar) sem delegar | pede para reavaliar o que falta e justificar em 1 linha se continuar sozinho |
| Fim de sessao com 20+ chamadas e nenhuma delegacao | segura o fim uma vez e pede a delegacao do que falta ou a justificativa; anota `auditoria` no historico |

Conta como delegacao: `maestro.py` no terminal ou subagente barato (`Explore`, `implementador`, `mecanico`). Carregar a skill nao conta. Executores lancados pelo maestro (`MAESTRO_EU`) e worktrees em `~/.maestro/wt` sao ignorados.

- `python ~/.maestro/guarda.py relatorio`: por sessao, quanto trabalho, quantas delegacoes, avisos e auditorias.
- Ajustes: `MAESTRO_LIMITE_AVISO` (12), `MAESTRO_LIMITE_PARADA` (20), `MAESTRO_GUARDA=0` desliga; `python instalar.py --sem-guarda` remove os hooks.

**Executor sem memoria.** O executor `claude` roda com `--settings ~/.maestro/executor-settings.json`, que desliga todos os plugins (o `ollama` ja usa `--bare`). Antes disso, o ai-memory gravava as sessoes dos executores como se fossem do usuario e aprendeu o "nao use a skill maestro" do prompt do executor como habito, injetando-o em toda sessao.
O `agy` nao tem como desligar hooks por chamada (o ai-memory se registra em `~/.gemini/config/hooks.json`), entao o maestro cria `~/.maestro/wt/.ai-memory.toml`, que vale para todo worktree: a sessao do executor continua registrada no projeto, mas nao contribui para o perfil (`[profile] contribute = false`), nao recebe o resumo dele (`consume = false`) e nao consome o handoff do orquestrador (`[handoff] deliver_on_session_start = false`). O executor tambem roda com `AI_MEMORY_AUTO_HANDOFF=off`, para nao criar handoff no fim da sessao, e as leituras e consultas do agy rodam em `~/.maestro/wt/_leitura` com o projeto adicionado por `--add-dir`, para o marcador valer tambem nelas. Um `.ai-memory.toml` versionado no proprio repositorio fica mais perto e tem prioridade; nesse caso, repita essas chaves nele.

## Testes

- `python -m unittest discover -s testes -v`: 83 testes de regressao, sem modelo e sem rede (aprendizado por tentativa e por margem para o melhor da rota, sequencia ruim, historico zerado ao mudar a configuracao, verificacao com bash no Git Bash, servico fora do ar, guarda, instalador e skills `validar` em HOME temporario, executor sem plugins, verificacao vazia, modelo local travado, rota por taxa de acerto, troca do gratis que falhou, marcador do `ai-memory`, loop de correcao automatica, `ganho --comparar`, `validar` (regua, revalidacao com diff desde a anterior, licoes e esforco), `juntar -C1`, folga de memoria para modelo local, agrupamento de apps liberaveis e `reabrir`).
- `python testes/smoke.py`: confere esta maquina (instalado == repo, hooks, memoria limpa, `rota`). Opcoes: `--modelo` (Haiku confirma que o executor nao ve o ai-memory), `--completo` (tarefa real ate a verificacao), `--inspiracoes` (headroom e rtk desde a ultima revisao), `--tudo`.

Rode os dois depois de mudar o maestro e antes de subir.

---

## Usando de qualquer CLI

O maestro e um comando de terminal; qualquer agente que roda shell pode usa-lo. A skill diz ao modelo como:

1. descobrir o proprio nivel pela tabela (ou pelo nome: pro/sonnet = 3, flash/haiku/mini = 2; na duvida, 2);
2. rodar `maestro rota --eu N` antes da primeira chamada;
3. seguir triagem, especificacao, conferencia e entrega.

| CLI | Como acha a skill | Testado como executor |
|---|---|---|
| Claude Code | `~/.claude/skills/maestro` | sim (`cc-haiku`): tarefa, verificacao, `--continuar` |
| agy (Antigravity) | `~/.gemini/antigravity-cli/skills/maestro` | sim (`agy-hydrogen-medium`): tarefa, verificacao, `--continuar` |
| opencode | `~/.claude/skills` (compativel) | sim (`omni-free-code`): tarefa, verificacao, `--continuar`, as duas vias da escalada |
| Gemini CLI | `~/.agents/skills/maestro` (`gemini skills list` mostra) | nao ha adaptador (ver limitacoes) |
| Codex e outros | `~/.agents/skills/maestro` | nao ha adaptador |

Se o shell do CLI nao expande `~`, use o caminho completo (no Windows, `$HOME\.maestro\maestro.py` no PowerShell).

---

## Dados e arquivos

```
repositorio
├── maestro.py        a ferramenta
├── catalogo.toml     executores, rotas e bolsos
├── SKILL.md          a skill (diretrizes do orquestrador)
├── validar-skill.md  modelo das skills /claudevalidateopus e /claudevalidateopusmax
├── warroom-skill.md  a skill /warroom (sala de guerra)
├── guarda.py         hooks que cobram a decisao de delegar
├── instalar.py       instalador (codigo, skills e hooks do guarda)
└── testes/           test_regressao.py (unittest) e smoke.py (esta maquina)

~/.maestro/
├── maestro.py, catalogo.toml   copia instalada
├── guarda.py, guarda/          guarda instalado e contadores por sessao
├── executor-settings.json      settings do executor claude (plugins desligados)
├── estado.json                 executores esgotados e ate quando
├── reabrir.json                apps fechados para modelo local e pendentes de reabertura
├── historico.jsonl             um evento por linha: run, leitura, consulta, validacao, cota, juntar, descartar, janela_local, reabrir
├── jobs/<job>/                 job.json, tarefa-rN.md, log-rN-<exec>.txt, verif-rN.txt, final.patch
├── validacoes/<id>/            pedido.md, diff.patch e resposta.md das revisoes com Opus (opus/opusmax)
├── wt/<job>/                   worktrees abertos
├── salas/<nome>/               sala de guerra: sala.json (frentes e eventos), <frente>.log, .tarefa.md, .res.json
├── travas/                     travas entre processos (worktree no mesmo repo, estado.json, sala)
├── wt/_leitura/                pasta de trabalho das leituras e consultas do agy
├── wt/.ai-memory.toml          marcador: sessoes de executor fora do perfil e sem handoff
└── respostas/                  respostas longas de leitura e consulta, na integra
```

O `historico.jsonl` alimenta `sugerir` e `ganho`. Cada `run` registra executor, tipo, rodada, status, verificacao, segundos, tokens, consultas feitas, tamanho da tarefa, tamanho do que voltou, avisos e linhas do diff.

---

## Limitacoes conhecidas

- **Gemini CLI como executor:** nao ha adaptador. No ambiente onde o maestro foi desenvolvido, o Gemini CLI esta configurado com chave de API e responde 401 no modo nao interativo; com login pela conta Google ele viraria mais um bolso gratuito.
- **`claude --bare`** reduziria ainda mais a base fixa do executor Claude, mas esse modo so aceita `ANTHROPIC_API_KEY` (nao le o login da assinatura) e cobraria por token, entao nao e usado. Limitar as ferramentas com `--tools` e o proximo passo a medir.
- **Custo fixo por chamada:** cada chamada a um CLI carrega o prompt de sistema dele (no opencode, uma pergunta de uma linha consumiu ~24 mil tokens de entrada). Por isso tarefas pequenas nao valem a delegacao.
- **Tokens "voltou" sao estimados** (caracteres/4); os de executor vem do proprio CLI.
- **Escalada depende do executor obedecer:** um modelo fraco pode chutar em vez de responder `ESCALAR:`. A verificacao e a conferencia continuam sendo a rede de seguranca.
- Desenvolvido e testado no **Windows 11** (Git Bash e PowerShell); o codigo e portavel, mas Linux/macOS foram menos exercitados.

---

## Inspiracoes

- [headroomlabs-ai/headroom](https://github.com/headroomlabs-ai/headroom): compressao reversivel (o original fica guardado e recuperavel), preservar erros e valores fora do padrao, nao tocar no que o usuario escreveu, pular o que e curto.
- [rtk-ai/rtk](https://github.com/rtk-ai/rtk): filtrar saida de comando antes de entrar no contexto, mostrar so falhas de teste, nunca imprimir mais que o original, falhar aberto, indicar como recuperar a saida completa, medir o ganho (`rtk gain`).
Revisado em 2026-11-16 (headroom no commit `5023c84e`, rtk 1.2.0): as ideias novas (Output Shaper, CacheAligner, pipelines/tasks do rtk) dependem de proxy na frente da API ou de filtros que podem esconder erro; nada foi incorporado alem do que esta acima. Desde a revisao anterior (`fda75de`) o headroom so trouxe correcoes do proxy, da memoria e dependencias, e o rtk nao lancou versao estavel (so `dev-1.2.1-rc`, com correcoes de hook, opencode e dotnet). `python testes/smoke.py --inspiracoes` avisa quando algum dos dois andar.
